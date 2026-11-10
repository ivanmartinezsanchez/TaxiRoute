"""Migración del vínculo con bases exclusivamente temporales."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta


class PruebasMigracionVinculo(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.usuario = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                                    contrasena="Conductor123!")
        # Reconstruir una base v11 sin asignarle un historial ficticio.
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TABLE intenciones_descartadas")
            conexion.execute("DROP TABLE barrera_revision")
            conexion.execute("DROP TABLE vinculo_historial")
            conexion.execute("PRAGMA user_version=11")

    def test_migra_sin_inferir_formato_y_conserva_datos(self):
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(VERSION_ESQUEMA, 13)
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertEqual(conexion.execute("SELECT nombre_usuario FROM usuarios WHERE id=?",
                                             (self.usuario.id,)).fetchone()[0], "Ana")
            self.assertEqual(conexion.execute("SELECT count(*) FROM vinculo_historial").fetchone()[0], 0)
        inicializar_base(self.ruta)

    def test_error_revierte_y_permite_reintento(self):
        with patch("taxiroute.base_cuentas.MIGRACION_VINCULO_V12", "CREATE TABLE (invalid)"):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertIsNone(conexion.execute("SELECT 1 FROM sqlite_schema WHERE name='vinculo_historial'").fetchone())
            self.assertEqual(conexion.execute("SELECT count(*) FROM usuarios").fetchone()[0], 1)
        inicializar_base(self.ruta)

    def test_tabla_ajena_preexistente_no_se_adopta(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("CREATE TABLE vinculo_historial (dato TEXT)")
            conexion.execute("INSERT INTO vinculo_historial VALUES ('conservar')")
        with self.assertRaises(sqlite3.OperationalError):
            inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertEqual(conexion.execute("SELECT dato FROM vinculo_historial").fetchone()[0], "conservar")

    def test_restricciones_del_vinculo(self):
        inicializar_base(self.ruta)
        identificador = "123e4567-e89b-12d3-a456-426614174000"
        with conectar(self.ruta) as conexion:
            insertar = "INSERT INTO vinculo_historial (id, formato, identificador, estado) VALUES (?, ?, ?, ?)"
            for fila in ((2, "csv", identificador, "listo"), (1, "json", identificador, "listo"),
                         (1, "csv", identificador.upper(), "listo"), (1, "csv", "x", "listo"),
                         (1, "csv", identificador, "desconocido")):
                with self.subTest(fila=fila), self.assertRaises(sqlite3.IntegrityError):
                    conexion.execute(insertar, fila)
            conexion.execute(insertar, (1, "csv", identificador, "preparando"))
            with self.assertRaises(sqlite3.IntegrityError):
                conexion.execute(insertar, (1, "sqlite", str(uuid4()), "listo"))
            conexion.execute("UPDATE vinculo_historial SET estado='listo' WHERE id=1")
