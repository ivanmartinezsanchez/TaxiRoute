"""Pruebas locales del aislamiento experimental de carreras por usuario."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest import TestCase
from unittest.mock import patch

from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.flota import FlotaEnMemoria, RevisionDesactualizada
from taxiroute.servicio import ServicioCarreras


class PruebasFlota(TestCase):
    def setUp(self):
        self.tiempo = 0
        self.tarifas = {EstadoVehiculo.PARADO: Decimal("0.02"), EstadoVehiculo.EN_MOVIMIENTO: Decimal("0.05")}
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo, tarifas=self.tarifas)))

    def test_dos_carreras_y_cambio_de_tarifas_sin_mezcla(self):
        self.flota.ejecutar("ana", "iniciar")
        self.flota.ejecutar("luis", "iniciar")
        self.tiempo = 10_000_000_000
        self.flota.ejecutar("ana", "mover")
        self.assertEqual(self.flota.consultar("luis").estado, "parado")
        self.tarifas = {EstadoVehiculo.PARADO: Decimal("0.03"), EstadoVehiculo.EN_MOVIMIENTO: Decimal("0.06")}
        self.tiempo = 20_000_000_000
        self.assertEqual(self.flota.ejecutar("ana", "finalizar").importe, Decimal("0.70"))
        self.assertEqual(self.flota.consultar("luis").importe, Decimal("0.40"))
        self.flota.ejecutar("ana", "iniciar")
        self.tiempo = 30_000_000_000
        self.assertEqual(self.flota.ejecutar("ana", "finalizar").importe, Decimal("0.30"))
        self.assertEqual(self.flota.ejecutar("luis", "finalizar").importe, Decimal("0.60"))

    def test_dos_inicios_concurrentes_para_un_usuario_no_crean_dos_carreras(self):
        barrera = Barrier(2)
        def iniciar():
            barrera.wait(timeout=5)
            return self.flota.ejecutar("ana", "iniciar")
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: iniciar(), range(2)))
        self.assertEqual(sum("Carrera iniciada" in r.mensaje for r in resultados), 1)
        self.assertEqual(sum("Ya hay una carrera activa" in r.mensaje for r in resultados), 1)
        self.assertTrue(self.flota.consultar("ana").activa)

    def test_revision_iguales_concurrentes_solo_una_se_ejecuta(self):
        barrera = Barrier(2)
        def iniciar():
            barrera.wait(timeout=5)
            try:
                self.flota.ejecutar_si_revision("ana", "iniciar", 0)
                return "aceptada"
            except RevisionDesactualizada:
                return "desactualizada"
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: iniciar(), range(2)))
        self.assertCountEqual(resultados, ["aceptada", "desactualizada"])
        self.assertTrue(self.flota.consultar("ana").activa)
        self.assertEqual(self.flota.consultar("ana").revision, 1)
    def test_captura_revision_y_carreras_separadas(self):
        self.assertIsNone(self.flota.capturar("ana"))
        self.flota.ejecutar("ana", "iniciar")
        self.flota.ejecutar("luis", "iniciar")
        self.tiempo = 10_000_000_000
        self.flota.ejecutar("ana", "mover")
        self.tiempo = 20_000_000_000
        ana = self.flota.capturar("ana")
        luis = self.flota.capturar("luis")
        self.assertEqual((ana.revision, ana.carrera.estado, ana.carrera.parado_ns,
                          ana.carrera.movimiento_ns), (2, EstadoVehiculo.EN_MOVIMIENTO,
                                                      10_000_000_000, 10_000_000_000))
        self.assertEqual((luis.revision, luis.carrera.estado, luis.carrera.parado_ns),
                         (1, EstadoVehiculo.PARADO, 20_000_000_000))
        self.assertNotEqual(ana.carrera.identificador, luis.carrera.identificador)
        self.assertEqual(self.flota.consultar("ana").revision, 2)
        self.flota.ejecutar("ana", "finalizar")
        self.assertIsNone(self.flota.capturar("ana"))
        self.assertIsNotNone(self.flota.capturar("luis"))

    def test_captura_y_cambio_concurrentes_comparten_bloqueo(self):
        self.flota.ejecutar("ana", "iniciar")
        self.tiempo = 10_000_000_000
        dentro_captura = Event()
        liberar = Event()
        intento_cambio = Event()
        cambio_terminado = Event()
        capturar_real = Carrera.capturar
        def capturar_con_pausa(carrera):
            dentro_captura.set()
            if not liberar.wait(3):
                raise TimeoutError("La prueba no liberó la captura")
            return capturar_real(carrera)
        def mover():
            intento_cambio.set()
            resultado = self.flota.ejecutar("ana", "mover")
            cambio_terminado.set()
            return resultado
        with patch.object(Carrera, "capturar", capturar_con_pausa):
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                futuro_captura = ejecutor.submit(self.flota.capturar, "ana")
                self.assertTrue(dentro_captura.wait(3))
                futuro_cambio = ejecutor.submit(mover)
                try:
                    self.assertTrue(intento_cambio.wait(3))
                    self.assertFalse(cambio_terminado.wait(0.15))
                finally:
                    liberar.set()
                captura = futuro_captura.result(timeout=3)
                cambio = futuro_cambio.result(timeout=3)
        self.assertEqual((captura.revision, captura.carrera.estado, captura.carrera.parado_ns),
                         (1, EstadoVehiculo.PARADO, 10_000_000_000))
        self.assertEqual((cambio.revision, cambio.estado), (2, "en movimiento"))
        self.assertEqual(self.flota.capturar("ana").revision, 2)
    def test_validacion_no_crea_servicios_y_estado_no_mutable(self):
        for usuario in (None, "", "  "):
            with self.assertRaises(ValueError):
                self.flota.ejecutar(usuario, "iniciar")
        for comando in ("salir", "historial", "exportar otro.csv", None):
            with self.assertRaises(ValueError):
                self.flota.ejecutar("ana", comando)
        self.assertIsNone(self.flota.consultar("ana"))
        estado = self.flota.ejecutar("ana", "iniciar")
        with self.assertRaises(AttributeError):
            estado.activa = False
        self.assertTrue(self.flota.consultar("ana").activa)

    def test_rechaza_reutilizar_una_misma_instancia(self):
        servicio = ServicioCarreras()
        flota = FlotaEnMemoria(lambda _: servicio)
        flota.ejecutar("ana", "iniciar")
        with self.assertRaises(ValueError):
            flota.ejecutar("luis", "iniciar")
        self.assertIsNone(flota.consultar("luis"))
        self.assertTrue(flota.consultar("ana").activa)