"""Estructura experimental de recuperación con bases exclusivamente temporales."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta


class PruebasEsquemaRecuperacion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "prueba.sqlite3"
        inicializar_base(self.ruta)
        self.usuario = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                                    contrasena="ConductorPrueba123!")

    def insertar(self, conexion, identificador, usuario, *, estado="activa", fin=None,
                 importe=None, parado=0, revision=0):
        conexion.execute("""INSERT INTO carreras_recuperacion
            (id, usuario_id, estado_registro, estado_vehiculo, inicio, ultimo_control, fin,
             parado_ns, movimiento_ns, tarifa_parado, tarifa_movimiento, importe_final, revision)
            VALUES (?, ?, ?, 'parado', ?, ?, ?, ?, 0, '0.02', '0.05', ?, ?)""",
            (identificador, usuario, estado, "2026-11-08T12:00:00+00:00",
             "2026-11-08T12:00:05+00:00", fin, parado, importe, revision))

    def preparar_version_5(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TABLE recibos_operacion")
            conexion.execute("DROP TABLE intenciones_operacion")
            conexion.execute("DROP TABLE carreras_recuperacion")
            conexion.execute("DROP TABLE codigos_recuperacion")
            conexion.execute("DROP TABLE vinculo_historial")
            conexion.execute("PRAGMA user_version = 5")

    def test_migracion_de_v5_conserva_usuario_y_crea_tabla(self):
        self.preparar_version_5()
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT nombre_usuario FROM usuarios WHERE id=?",
                                             (self.usuario.id,)).fetchone()[0], "Ana")
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 0)
        inicializar_base(self.ruta)

    def test_migracion_fallida_se_revierte_y_permite_reintento(self):
        self.preparar_version_5()
        with patch("taxiroute.base_cuentas.MIGRACION_RECUPERACION", ("CREATE TABLE temporal (id INTEGER)", "SQL INVALIDO")):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='temporal'").fetchone())
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
        inicializar_base(self.ruta)

    def test_una_pendiente_por_usuario_y_guardadas_conservadas(self):
        with conectar(self.ruta) as conexion:
            self.insertar(conexion, "a", self.usuario.id)
            with self.assertRaises(sqlite3.IntegrityError):
                self.insertar(conexion, "b", self.usuario.id, estado="revision_pendiente")
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada', fin=?, importe_final=? WHERE id='a'",
                             ("2026-11-08T12:01:00+00:00", "1.20"))
            self.insertar(conexion, "b", self.usuario.id)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 2)

    def test_rechaza_cuenta_inexistente_y_datos_incoherentes(self):
        with conectar(self.ruta) as conexion:
            for kwargs in ({"usuario": "inexistente"}, {"usuario": self.usuario.id, "parado": -1},
                           {"usuario": self.usuario.id, "revision": -1},
                           {"usuario": self.usuario.id, "estado": "guardada"},
                           {"usuario": self.usuario.id, "estado": "desconocido"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(sqlite3.IntegrityError):
                    self.insertar(conexion, "x", **kwargs)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 0)
