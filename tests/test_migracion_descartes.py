"""Migración y protección de reintentos con bases exclusivamente temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.revision_durable import RevisionDurableInvalida, revision_durable
from taxiroute.sesiones import ServicioSesiones


class PruebasMigracionDescartes(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token_ana = self.sesiones.iniciar_conductor("Ana", "Conductor123!").token
        self.token_luis = self.sesiones.iniciar_conductor("Luis", "Conductor123!").token

    def version_12(self):
        with conectar(self.base) as conexion:
            conexion.execute("DROP TABLE intenciones_descartadas")
            conexion.execute("DROP TABLE barrera_revision")
            conexion.execute("PRAGMA user_version=12")

    def test_migracion_conserva_cuentas_y_sesiones(self):
        self.version_12()
        inicializar_base(self.base)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT count(*) FROM usuarios").fetchone()[0], 2)
            self.assertEqual(conexion.execute("SELECT count(*) FROM sesiones").fetchone()[0], 2)
            self.assertEqual(conexion.execute("SELECT count(*) FROM intenciones_descartadas").fetchone()[0], 0)
            self.assertEqual(conexion.execute("SELECT count(*) FROM barrera_revision").fetchone()[0], 0)
        inicializar_base(self.base)

    def test_error_migracion_revierte_ambas_tablas(self):
        from taxiroute.base_cuentas import MIGRACION_DESCARTES_V13
        self.version_12()
        with patch("taxiroute.base_cuentas.MIGRACION_DESCARTES_V13", (MIGRACION_DESCARTES_V13[0], "CREATE TABLE invalida (")):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.base)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 12)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='intenciones_descartadas'").fetchone())
            self.assertEqual(conexion.execute("SELECT count(*) FROM usuarios").fetchone()[0], 2)
        inicializar_base(self.base)

    def test_rechaza_reutilizar_id_descartado_incluso_otra_cuenta(self):
        identificador = str(uuid4())
        with conectar(self.base) as conexion:
            conexion.execute("""INSERT INTO intenciones_descartadas
                (id, usuario_id, actor_id, accion, revision, carrera_id, creada_en, resuelta_en, huella)
                VALUES (?, ?, ?, 'iniciar', 0, NULL, ?, ?, ?)""",
                (identificador, self.ana.id, self.ana.id, self.fecha.isoformat(), self.fecha.isoformat(), "a" * 64))
        for token in (self.token_ana, self.token_luis):
            with self.subTest(token=token[:3]), self.assertRaises(IntencionNoPermitida):
                registrar_intencion(self.sesiones, token, id_intencion=identificador, accion="iniciar", revision=0)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT count(*) FROM intenciones_operacion").fetchone()[0], 0)

    def test_barrera_rechaza_revision_vieja_con_identificador_distinto(self):
        with conectar(self.base) as conexion:
            conexion.execute("INSERT INTO barrera_revision(usuario_id, revision) VALUES (?, ?)", (self.ana.id, 1))
        with self.assertRaises(IntencionNoPermitida):
            registrar_intencion(self.sesiones, self.token_ana, id_intencion=str(uuid4()), accion="iniciar", revision=0)
        resultado = registrar_intencion(self.sesiones, self.token_ana, id_intencion=str(uuid4()), accion="iniciar", revision=1)
        self.assertEqual(resultado.revision, 1)

    def test_barrera_tambien_rechaza_movimiento_antiguo(self):
        punto = PuntoControl("carrera-prueba", self.ana.id, self.fecha, self.fecha + timedelta(seconds=5),
                             "parado", 5_000_000_000, 0, "0.02", "0.05", 1)
        guardar_punto(self.base, punto)
        with conectar(self.base) as conexion:
            conexion.execute("INSERT INTO barrera_revision(usuario_id, revision) VALUES (?, ?)", (self.ana.id, 2))
        with self.assertRaises(IntencionNoPermitida):
            registrar_intencion(self.sesiones, self.token_ana, id_intencion=str(uuid4()), accion="mover", revision=1, carrera_id=punto.carrera_id)

    def test_barrera_corrupta_falla_cerrado(self):
        with conectar(self.base) as conexion:
            conexion.execute("PRAGMA ignore_check_constraints=ON")
            conexion.execute("INSERT INTO barrera_revision(usuario_id, revision) VALUES (?, ?)", (self.ana.id, -1))
            with self.assertRaises(RevisionDurableInvalida):
                revision_durable(conexion, self.ana.id)

    def test_restricciones_de_huella_y_actor(self):
        with conectar(self.base) as conexion:
            sentencia = """INSERT INTO intenciones_descartadas
                (id, usuario_id, actor_id, accion, revision, carrera_id, creada_en, resuelta_en, huella)
                VALUES (?, ?, ?, 'iniciar', 0, NULL, ?, ?, ?)"""
            with self.assertRaises(sqlite3.IntegrityError):
                conexion.execute(sentencia, (str(uuid4()), self.ana.id, self.ana.id, self.fecha.isoformat(), self.fecha.isoformat(), "z" * 64))
            with self.assertRaises(sqlite3.IntegrityError):
                conexion.execute(sentencia, (str(uuid4()), self.ana.id, "no-existe", self.fecha.isoformat(), self.fecha.isoformat(), "a" * 64))
