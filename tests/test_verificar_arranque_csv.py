"""Comprobaciones de arranque solo con bases y archivos temporales."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import ConciliacionCSVError, conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final
from taxiroute.puntos_control import PuntoControl
from taxiroute.verificar_arranque_csv import verificar_historiales_guardados_csv


class PruebasVerificacionArranqueCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        raiz = Path(temporal.name)
        self.base, self.carpeta = raiz / "cuentas.sqlite3", raiz / "historiales"
        self.carpeta.mkdir()
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)

    def verificar(self):
        return verificar_historiales_guardados_csv(self.base, self.carpeta)

    def guardar(self, cuenta, numero):
        punto = PuntoControl(f"carrera-{numero}", cuenta.id, self.fecha, self.fecha + timedelta(seconds=10),
                             "parado", 10_000_000_000, 0, "0.02", "0.05", numero)
        preparar_punto_final(self.base, punto, punto.ultimo_control)
        csv = self.carpeta / cuenta.id / "historial.csv"
        conciliar_pendiente_csv(self.base, csv, cuenta.id, punto.carrera_id)
        return csv

    def test_sin_comprobantes_no_modifica_historial_ajeno(self):
        archivo = self.carpeta / "antiguo.csv"
        archivo.write_text("historial antiguo sin identificar", encoding="utf-8")
        self.assertEqual(self.verificar(), 0)
        self.assertEqual(archivo.read_text(encoding="utf-8"), "historial antiguo sin identificar")

    def test_verifica_cierres_de_cuentas_distintas(self):
        uno, dos = self.guardar(self.ana, 1), self.guardar(self.luis, 2)
        antes = (uno.read_bytes(), dos.read_bytes())
        self.assertEqual(self.verificar(), 2)
        self.assertEqual((uno.read_bytes(), dos.read_bytes()), antes)

    def test_archivo_ausente_se_rechaza_sin_recrearlo(self):
        csv = self.guardar(self.ana, 1)
        csv.unlink()
        with self.assertRaises(ConciliacionCSVError):
            self.verificar()
        self.assertFalse(csv.exists())

    def test_fila_borrada_modificada_o_duplicada_se_rechaza(self):
        csv = self.guardar(self.ana, 1)
        cabecera, fila = csv.read_text(encoding="utf-8").splitlines(keepends=True)
        for contenido in (cabecera, cabecera + fila.replace(",0.20\n", ",9.99\n"), cabecera + fila + fila):
            with self.subTest(contenido=contenido):
                csv.write_text(contenido, encoding="utf-8", newline="")
                with self.assertRaises(ConciliacionCSVError):
                    self.verificar()
                self.assertEqual(csv.read_text(encoding="utf-8"), contenido)

    def test_formato_incompleto_y_esquema_incompatible_se_rechazan(self):
        csv = self.guardar(self.ana, 1)
        csv.write_text("incompleto", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.verificar()
        self.assertEqual(csv.read_text(encoding="utf-8"), "incompleto")
        with conectar(self.base) as conexion:
            conexion.execute("PRAGMA user_version=999")
        with self.assertRaises(ConciliacionCSVError):
            self.verificar()

    def test_cambio_de_propietarios_durante_el_arranque_se_rechaza(self):
        csv = self.guardar(self.ana, 1)
        from taxiroute.bloqueo import bloqueo_historial
        @contextmanager
        def bloquear_y_guardar_otra(ruta):
            self.guardar(self.luis, 2)
            with bloqueo_historial(ruta):
                yield
        with patch("taxiroute.verificar_arranque_csv.bloqueo_historial", bloquear_y_guardar_otra):
            with self.assertRaises(ConciliacionCSVError):
                self.verificar()
        self.assertTrue(csv.exists())
