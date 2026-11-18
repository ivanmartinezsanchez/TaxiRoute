"""Historiales y cuentas exclusivamente temporales para la consulta verificada."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import ConciliacionCSVError, conciliar_pendiente_csv_autorizado
from taxiroute.consulta_historial_csv import consultar_historial_csv_verificado
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final
from taxiroute.puntos_control import PuntoControl
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import SesionNoAutorizada, ServicioSesiones


class PruebasConsultaCSVVerificada(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.base = carpeta / "cuentas.sqlite3"
        self.historiales = carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "Conductor123!").token
        self.otro_token = self.sesiones.iniciar_conductor("Luis", "Conductor123!").token
        self.csv = self.historiales / self.ana.id / "historial.csv"

    def consultar(self, token=None):
        return consultar_historial_csv_verificado(self.sesiones, token or self.token, self.historiales)

    def preparar(self, guardar=True):
        punto = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha + timedelta(seconds=10),
                             "parado", 10_000_000_000, 0, "0.02", "0.05", 1)
        preparar_punto_final(self.base, punto, punto.ultimo_control)
        if guardar:
            conciliar_pendiente_csv_autorizado(self.sesiones, self.token, self.historiales, punto.carrera_id)

    def test_usuario_nuevo_y_aislamiento(self):
        self.assertEqual(self.consultar(), [])
        self.assertFalse(self.csv.exists())
        self.preparar()
        self.assertEqual(len(self.consultar()), 1)
        self.assertEqual(self.consultar(self.otro_token), [])

    def test_archivo_borrado_no_se_recrea_ni_aparece_como_vacio(self):
        self.preparar()
        self.csv.unlink()
        with self.assertRaises(ConciliacionCSVError):
            self.consultar()
        self.assertFalse(self.csv.exists())

    def test_fila_alterada_borrada_o_duplicada_se_detecta(self):
        self.preparar()
        original = self.csv.read_text(encoding="utf-8")
        cabecera, fila = original.splitlines(keepends=True)
        for contenido in (cabecera, cabecera + fila.replace(",0.20\n", ",9.99\n"), cabecera + fila + fila):
            with self.subTest(contenido=contenido):
                self.csv.write_text(contenido, encoding="utf-8", newline="")
                with self.assertRaises(ConciliacionCSVError):
                    self.consultar()
                self.assertEqual(self.csv.read_text(encoding="utf-8"), contenido)

    def test_csv_incompleto_no_se_repara(self):
        self.preparar()
        self.csv.write_text("incompleto", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.consultar()
        self.assertEqual(self.csv.read_text(encoding="utf-8"), "incompleto")

    def test_cierre_pendiente_no_se_muestra_como_confirmado(self):
        self.preparar(guardar=False)
        with self.assertRaises(ConciliacionCSVError):
            self.consultar()
        self.assertFalse(self.csv.exists())

    def test_interrupcion_despues_de_escribir_no_muestra_cierre_pendiente(self):
        self.preparar(guardar=False)
        with patch("taxiroute.conciliacion_csv._verificar_csv", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                conciliar_pendiente_csv_autorizado(self.sesiones, self.token, self.historiales, "carrera-1")
        self.assertTrue(self.csv.exists())
        with self.assertRaises(ConciliacionCSVError):
            self.consultar()
        conciliar_pendiente_csv_autorizado(self.sesiones, self.token, self.historiales, "carrera-1")
        self.assertEqual(len(self.consultar()), 1)

    def test_lectura_y_conciliacion_simultaneas_no_muestran_datos_parciales(self):
        self.preparar(guardar=False)
        barrera = Barrier(2)
        def guardar():
            barrera.wait(timeout=5)
            conciliar_pendiente_csv_autorizado(self.sesiones, self.token, self.historiales, "carrera-1")
        def leer():
            barrera.wait(timeout=5)
            try:
                return self.consultar()
            except ConciliacionCSVError:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            escritura, lectura = ejecutor.submit(guardar), ejecutor.submit(leer)
            escritura.result(timeout=10)
            resultado = lectura.result(timeout=10)
        self.assertTrue(resultado is None or len(resultado) == 1)
        self.assertEqual(len(self.consultar()), 1)

    def test_sesion_revocada_tras_elegir_ruta_no_lee(self):
        self.preparar()
        def seleccionar_y_revocar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.sesiones.cerrar(self.token)
            return historial
        with patch("taxiroute.consulta_historial_csv._historial_de_cuenta", side_effect=seleccionar_y_revocar):
            with self.assertRaises(SesionNoAutorizada):
                self.consultar()

    def test_sesion_caduca_durante_lectura(self):
        from taxiroute.historial import HistorialCSV
        self.preparar()
        leer = HistorialCSV._consultar_sin_bloqueo
        def leer_y_avanzar(historial):
            filas = leer(historial)
            self.fecha += timedelta(hours=1)
            return filas
        with patch.object(HistorialCSV, "_consultar_sin_bloqueo", leer_y_avanzar):
            with self.assertRaises(SesionNoAutorizada):
                self.consultar()
