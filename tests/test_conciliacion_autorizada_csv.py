"""Comprobaciones con cuentas, carreras y archivos temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import conciliar_pendiente_csv_autorizado
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final
from taxiroute.historial import HistorialCSV
from taxiroute.puntos_control import PuntoControl
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import SesionNoAutorizada, ServicioSesiones


class PruebasConciliacionAutorizada(TestCase):
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
        punto = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha + timedelta(seconds=10),
                             "parado", 10_000_000_000, 0, "0.02", "0.05", 1)
        preparar_punto_final(self.base, punto, punto.ultimo_control)
        self.csv = self.historiales / self.ana.id / "historial.csv"

    def conciliar(self, token=None):
        return conciliar_pendiente_csv_autorizado(self.sesiones, token or self.token,
                                                  self.historiales, "carrera-1")

    def estado(self):
        with conectar(self.base) as conexion:
            return conexion.execute("SELECT estado_registro FROM carreras_recuperacion").fetchone()[0]

    def test_guarda_y_reintenta_en_historial_propio(self):
        self.assertFalse(self.conciliar().ya_guardada)
        self.assertTrue(self.conciliar().ya_guardada)
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)
        self.assertEqual(self.estado(), "guardada")

    def test_cuenta_ajena_no_puede_guardar(self):
        with self.assertRaises(ValueError):
            self.conciliar(self.otro_token)
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_sesion_revocada_antes_no_escribe(self):
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.conciliar()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_revocacion_tras_elegir_ruta_no_escribe(self):
        def seleccionar_y_revocar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.sesiones.cerrar(self.token)
            return historial
        with patch("taxiroute.rutas_csv._historial_de_cuenta", side_effect=seleccionar_y_revocar):
            with self.assertRaises(SesionNoAutorizada):
                self.conciliar()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_sesion_caducada_antes_de_transaccion_no_escribe(self):
        def seleccionar_y_avanzar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.fecha += timedelta(hours=1)
            return historial
        with patch("taxiroute.rutas_csv._historial_de_cuenta", side_effect=seleccionar_y_avanzar):
            with self.assertRaises(SesionNoAutorizada):
                self.conciliar()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")
