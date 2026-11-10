"""Recibos experimentales con datos y bases temporales."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base, VERSION_ESQUEMA
from taxiroute.cuentas import crear_cuenta
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.puntos_control import PuntoControl, _guardar_punto_en_transaccion, leer_punto
from taxiroute.recibos_operacion import consultar_recibo, guardar_recibo_en_transaccion
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasRecibosOperacion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=self.identificador,
                            accion="iniciar", revision=0)
        self.punto = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha,
                                  "parado", 0, 0, "0.02", "0.05", 1)

    def guardar(self, conexion, punto=None):
        return guardar_recibo_en_transaccion(conexion, id_intencion=self.identificador,
            usuario_id=self.ana.id, accion="iniciar", revision=0,
            punto=punto or self.punto, creado_en=self.fecha)

    def test_operacion_atomica_y_reintento_identico(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            _guardar_punto_en_transaccion(conexion, self.punto)
            primero = self.guardar(conexion)
            conexion.execute("DELETE FROM intenciones_operacion WHERE id=?", (self.identificador,))
            conexion.commit()
        self.assertEqual(consultar_recibo(self.sesiones, self.token, self.identificador), primero)
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.guardar(conexion), primero)
            with self.assertRaises(IntencionNoPermitida):
                self.guardar(conexion, replace(self.punto, parado_ns=1))
            conexion.rollback()
        self.assertEqual(leer_punto(self.ruta, self.ana.id, self.punto.carrera_id), self.punto)

    def test_fallo_antes_commit_revierte_punto_recibo_e_intencion(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            _guardar_punto_en_transaccion(conexion, self.punto)
            self.guardar(conexion)
            conexion.execute("DELETE FROM intenciones_operacion WHERE id=?", (self.identificador,))
            conexion.rollback()
        self.assertIsNone(consultar_recibo(self.sesiones, self.token, self.identificador))
        self.assertIsNone(leer_punto(self.ruta, self.ana.id, self.punto.carrera_id))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)

    def test_lectura_aislada_y_sesion_vigente(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            _guardar_punto_en_transaccion(conexion, self.punto)
            self.guardar(conexion)
            conexion.commit()
        token_luis = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        self.assertIsNone(consultar_recibo(self.sesiones, token_luis, self.identificador))
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            consultar_recibo(self.sesiones, self.token, self.identificador)

    def test_migracion_v9_conserva_datos_y_revierte_si_falla(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TABLE recibos_operacion")
            conexion.execute("DROP TABLE codigos_recuperacion")
            conexion.execute("DROP TABLE intenciones_descartadas")
            conexion.execute("DROP TABLE barrera_revision")
            conexion.execute("DROP TABLE vinculo_historial")
            conexion.execute("PRAGMA user_version=9")
        with patch("taxiroute.base_cuentas.MIGRACION_RECIBOS_V10",
                   ("CREATE TABLE prueba_temporal (x INTEGER)", "SQL INVALIDO")):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='prueba_temporal'").fetchone())
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 2)
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 2)
