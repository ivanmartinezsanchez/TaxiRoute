"""Pruebas del esquema, sin cuentas reales ni historial de uso."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import (
    EsquemaCuentasError, VERSION_ESQUEMA, MIGRACION_AUDITORIA, MIGRACION_AUDITORIA_V7, conectar, inicializar_base,
)


class PruebasBaseCuentas(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)

    def insertar(self, conexion, identificador="cuenta-1", nombre="ana", rol="conductor"):
        # Valor ficticio: estas pruebas validan SQL, no el algoritmo de contraseña.
        conexion.execute(
            """INSERT INTO usuarios
               (id, nombre_usuario, nombre_usuario_normalizado, nombre_visible,
                rol, hash_contrasena, creado_en, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (identificador, nombre, nombre, "Cuenta de prueba", rol,
             "verificador-ficticio-no-utilizable",
             "2026-11-08T10:00:00+00:00", "2026-11-08T10:00:00+00:00"),
        )

    def test_creacion_vacia_versionada_y_configuracion_por_conexion(self):
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)

    def test_reabrir_e_inicializar_conserva_bloqueo_y_datos(self):
        with conectar(self.ruta) as conexion:
            self.insertar(conexion)
            conexion.execute(
                "UPDATE usuarios SET intentos_fallidos=15, bloqueado_en=?, habilitado=0 WHERE id=?",
                ("2026-11-08T10:01:00+00:00", "cuenta-1"),
            )
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            fila = conexion.execute("SELECT * FROM usuarios").fetchone()
            self.assertEqual(fila["nombre_usuario"], "ana")
            self.assertEqual(fila["intentos_fallidos"], 15)
            self.assertEqual(fila["habilitado"], 0)
            self.assertIsNotNone(fila["bloqueado_en"])

    def test_rechaza_duplicados_roles_y_valores_invalidos(self):
        with conectar(self.ruta) as conexion:
            self.insertar(conexion)
            with self.assertRaises(sqlite3.IntegrityError):
                self.insertar(conexion, identificador="cuenta-2")
            with self.assertRaises(sqlite3.IntegrityError):
                self.insertar(conexion, identificador="cuenta-3", nombre="bea", rol="superusuario")
            for campo, valor in (
                ("intentos_fallidos", -1), ("habilitado", 2),
                ("cambio_obligatorio", 2), ("version_credenciales", 0),
                ("nombre_usuario_normalizado", ""), ("intentos_fallidos", 1.5),
            ):
                with self.subTest(campo=campo), self.assertRaises(sqlite3.IntegrityError):
                    # Los nombres de columna proceden exclusivamente de esta lista fija.
                    conexion.execute(f"UPDATE usuarios SET {campo}=?", (valor,))

    def test_version_futura_se_rechaza_sin_modificar_datos(self):
        with conectar(self.ruta) as conexion:
            self.insertar(conexion)
            conexion.execute("PRAGMA user_version = 99")
        with self.assertRaises(EsquemaCuentasError):
            inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)

    def test_base_ajena_no_se_adopta(self):
        otra = self.ruta.parent / "otra.sqlite3"
        with conectar(otra) as conexion:
            conexion.execute("CREATE TABLE datos (valor TEXT)")
            conexion.execute("INSERT INTO datos VALUES (?)", ("conservar",))
        with self.assertRaises(EsquemaCuentasError):
            inicializar_base(otra)
        with conectar(otra) as conexion:
            self.assertEqual(conexion.execute("SELECT valor FROM datos").fetchone()[0], "conservar")
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_error_de_creacion_revierte_y_permite_reintentar(self):
        nueva = self.ruta.parent / "nueva.sqlite3"
        with patch("taxiroute.base_cuentas.CREAR_USUARIOS", "SQL INVALIDO"):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(nueva)
        with conectar(nueva) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(conexion.execute("PRAGMA application_id").fetchone()[0], 0)
        inicializar_base(nueva)

    def preparar_version_6_con_auditoria(self):
        with conectar(self.ruta) as conexion:
            # Recrea el esquema real de la versión antigua, sin tablas futuras.
            conexion.execute("DROP TABLE recibos_operacion")
            conexion.execute("DROP TABLE intenciones_operacion")
            self.insertar(conexion, identificador="admin", nombre="admin", rol="administrador")
            self.insertar(conexion, identificador="conductor", nombre="luis")
            conexion.execute("DROP TABLE auditoria_cuentas")
            conexion.execute(MIGRACION_AUDITORIA)
            conexion.execute("INSERT INTO auditoria_cuentas(id, actor_id, afectado_id, accion, fecha) VALUES (7, ?, ?, 'revocar', ?)",
                             ("admin", "conductor", "2026-11-08T10:00:00+00:00"))
            conexion.execute("PRAGMA user_version=6")

    def test_migracion_6_a_7_conserva_auditoria_y_admite_alta(self):
        self.preparar_version_6_con_auditoria()
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            fila = conexion.execute("SELECT * FROM auditoria_cuentas WHERE id=7").fetchone()
            self.assertEqual((fila["actor_id"], fila["afectado_id"], fila["accion"]),
                             ("admin", "conductor", "revocar"))
            conexion.execute("INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha) VALUES (?, ?, 'alta', ?)",
                             ("admin", "conductor", "2026-11-09T10:00:00+00:00"))
            with self.assertRaises(sqlite3.IntegrityError):
                conexion.execute("INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha) VALUES (?, ?, 'otra', ?)",
                                 ("admin", "conductor", "2026-11-09T10:00:00+00:00"))
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM auditoria_cuentas").fetchone()[0], 2)
        inicializar_base(self.ruta)

    def test_fallo_migracion_auditoria_revierte_y_permite_reintento(self):
        self.preparar_version_6_con_auditoria()
        sentencias = (*MIGRACION_AUDITORIA_V7[:2], "SQL INVALIDO")
        with patch("taxiroute.base_cuentas.MIGRACION_AUDITORIA_V7", sentencias):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(conexion.execute("SELECT accion FROM auditoria_cuentas WHERE id=7").fetchone()[0], "revocar")
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='auditoria_cuentas_v7'").fetchone())
        inicializar_base(self.ruta)

    def test_migracion_rechaza_trigger_existente_sin_eliminarlo(self):
        self.preparar_version_6_con_auditoria()
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER auditoria_personalizada BEFORE INSERT ON auditoria_cuentas
                BEGIN SELECT 1; END""")
        with self.assertRaises(EsquemaCuentasError):
            inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertIsNotNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='auditoria_personalizada'").fetchone())
            self.assertEqual(conexion.execute("SELECT accion FROM auditoria_cuentas WHERE id=7").fetchone()[0], "revocar")
    def test_inicializacion_concurrente_no_duplica_ni_falla(self):
        nueva = self.ruta.parent / "concurrente.sqlite3"
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            list(ejecutor.map(inicializar_base, [nueva, nueva]))
        with conectar(nueva) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
