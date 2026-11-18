"""Migración del esquema de cuentas en bases temporales."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta


class PruebasMigracionCodigos(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.cuenta = crear_cuenta(self.ruta, nombre_usuario="AdminPrueba", nombre_visible="Prueba",
                                   contrasena="AdminPrueba123!", rol="administrador")
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TABLE codigos_recuperacion")
            conexion.execute("DROP TABLE revisiones_csv_ausente")
            conexion.execute("DROP TABLE procedencia_sqlite")
            conexion.execute("DROP TABLE intenciones_descartadas")
            conexion.execute("DROP TABLE barrera_revision")
            conexion.execute("DROP TABLE vinculo_historial")
            conexion.execute("PRAGMA user_version=10")

    def test_migra_version_10_conservando_cuenta_y_sin_generar_codigos(self):
        inicializar_base(self.ruta)
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT id FROM usuarios").fetchone()[0], self.cuenta.id)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)

    def test_fallo_de_migracion_revierte_y_conserva_version_y_cuenta(self):
        with patch("taxiroute.base_cuentas.CREAR_TABLA_CODIGOS", "SQL INVALIDO"):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 10)
            self.assertEqual(conexion.execute("SELECT id FROM usuarios").fetchone()[0], self.cuenta.id)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='codigos_recuperacion'").fetchone())

    def test_tabla_ajena_no_se_sobrescribe(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("CREATE TABLE codigos_recuperacion (dato TEXT)")
            conexion.execute("INSERT INTO codigos_recuperacion VALUES ('conservar')")
        with self.assertRaises(sqlite3.OperationalError):
            inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 10)
            self.assertEqual(conexion.execute("SELECT dato FROM codigos_recuperacion").fetchone()[0], "conservar")


if __name__ == "__main__":
    unittest.main()
