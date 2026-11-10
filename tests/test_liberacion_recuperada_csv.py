"""Recuperación CSV del último punto usando solo datos temporales."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import ConciliacionCSVError, conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.historial import HistorialCSV
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.liberacion_recuperada_csv import liberar_recuperada_csv
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.recuperacion_ultima_captura import cerrar_en_ultima_captura, huella_revision
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import SesionNoAutorizada, ServicioSesiones


class PruebasRecuperacionCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.base = carpeta / "cuentas.sqlite3"
        self.historiales = carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        inicio = datetime(2026, 11, 10, 10, tzinfo=timezone.utc)
        self.fecha = inicio + timedelta(hours=4)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.otro = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        self.punto = PuntoControl("carrera-1", self.ana.id, inicio, inicio + timedelta(seconds=30),
                                  "en movimiento", 10_000_000_000, 20_000_000_000, "0.02", "0.05", 1)
        guardar_punto(self.base, self.punto)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(), usuarios_pendientes=[self.ana.id],
                                    modo_durable_experimental=True, revisiones_iniciales={self.ana.id: 1})
        self.huella = huella_revision(self.punto, 1)
        self.csv = self.historiales / self.ana.id / "historial.csv"

    def preparar_y_conciliar(self):
        resumen = cerrar_en_ultima_captura(self.sesiones, self.token, self.flota,
            revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control, huella_esperada=self.huella)
        conciliar_pendiente_csv(self.base, self.csv, self.ana.id, resumen.punto.carrera_id)
        return resumen

    def liberar(self, *, token=None, huella=None):
        return liberar_recuperada_csv(self.sesiones, token or self.token, self.flota, self.historiales,
            revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control,
            huella_esperada=self.huella if huella is None else huella)

    def test_cierra_ultimo_punto_sin_cobrar_tiempo_desconocido_y_reintenta(self):
        resumen = self.preparar_y_conciliar()
        self.assertEqual((resumen.fin, str(resumen.importe)), (self.punto.ultimo_control, "1.20"))
        self.assertEqual(str(self.liberar().importe), "1.20")
        self.assertEqual(str(self.liberar().importe), "1.20")
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador, accion="iniciar", revision=2)
        self.assertEqual(self.flota.aplicar_intencion_experimental(self.sesiones, self.token, identificador).revision, 3)
        with self.assertRaises(FlotaBloqueada):
            self.liberar()

    def test_huella_erronea_o_csv_alterado_no_liberan(self):
        self.preparar_y_conciliar()
        with self.assertRaises(OperacionNoPermitida):
            self.liberar(huella="0" * 64)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        contenido = self.csv.read_text(encoding="utf-8")
        self.csv.write_text(contenido.replace(",1.20\n", ",999.00\n"), encoding="utf-8", newline="")
        with self.assertRaises(ConciliacionCSVError):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_exige_una_huella_de_revision_valida(self):
        self.preparar_y_conciliar()
        for huella in (None, "", "G" * 64):
            with self.subTest(tipo=type(huella).__name__), self.assertRaises(OperacionNoPermitida):
                liberar_recuperada_csv(self.sesiones, self.token, self.flota, self.historiales,
                    revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control, huella_esperada=huella)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_sin_historial_o_usuario_ajeno_no_libera(self):
        self.preparar_y_conciliar()
        with self.assertRaises((OperacionNoPermitida, FlotaBloqueada)):
            self.liberar(token=self.otro)
        self.csv.unlink()
        with self.assertRaises(OperacionNoPermitida):
            self.liberar()
        self.assertFalse(self.csv.exists())
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_sesion_revocada_durante_seleccion_no_libera(self):
        from taxiroute.liberacion_recuperada_csv import _historial_de_cuenta
        self.preparar_y_conciliar()
        def seleccionar_y_revocar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.sesiones.cerrar(self.token)
            return historial
        with patch("taxiroute.liberacion_recuperada_csv._historial_de_cuenta", side_effect=seleccionar_y_revocar):
            with self.assertRaises(SesionNoAutorizada):
                self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_dos_confirmaciones_identicas_no_duplican_historial(self):
        self.preparar_y_conciliar()
        barrera = Barrier(2)
        def intentar(_):
            barrera.wait(timeout=5)
            return self.liberar()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, range(2)))
        self.assertEqual([str(resultado.importe) for resultado in resultados], ["1.20", "1.20"])
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_error_al_cerrar_transaccion_no_libera_y_permite_reintentar(self):
        from taxiroute.liberacion_recuperada_csv import _conectar_existente
        self.preparar_y_conciliar()
        def conexion_con_fallo(ruta):
            class Proxy:
                def __init__(self, real):
                    self.real, self.fallo = real, False
                def __getattr__(self, nombre):
                    return getattr(self.real, nombre)
                def rollback(self):
                    self.real.rollback()
                    if not self.fallo:
                        self.fallo = True
                        raise OSError("fallo simulado")
            return Proxy(_conectar_existente(ruta))
        with patch("taxiroute.liberacion_recuperada_csv._conectar_existente", side_effect=conexion_con_fallo):
            with self.assertRaises(OSError):
                self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(str(self.liberar().importe), "1.20")


if __name__ == "__main__":
    unittest.main()
