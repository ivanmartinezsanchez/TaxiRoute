"""Confirmación experimental con SQLite temporal, sin flota ni datos reales."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.completar_intencion import completar_intencion
from taxiroute.cuentas import crear_cuenta
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.puntos_control import PuntoControl, leer_punto
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasCompletarIntencion(unittest.TestCase):
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
        self.id = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id, accion="iniciar", revision=0)
        self.punto = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha,
                                  "parado", 0, 0, "0.02", "0.05", 1)

    def completar(self, punto=None, token=None):
        return completar_intencion(self.sesiones, token or self.token, self.id, punto or self.punto)

    def pendiente(self):
        with conectar(self.ruta) as conexion:
            return conexion.execute("SELECT COUNT(*) FROM intenciones_operacion WHERE id=?", (self.id,)).fetchone()[0]

    def test_inicio_y_cambio_guardan_punto_y_eliminan_intencion(self):
        self.assertEqual(self.completar(), self.punto)
        self.assertEqual(self.pendiente(), 0)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, "carrera-1"), self.punto)
        self.id = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id, accion="mover",
                            revision=1, carrera_id="carrera-1")
        nuevo = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha + timedelta(seconds=1),
                             "en movimiento", 1_000_000_000, 0, "0.02", "0.05", 2)
        self.assertEqual(self.completar(nuevo), nuevo)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, "carrera-1"), nuevo)
        self.assertEqual(self.pendiente(), 0)

    def test_propietario_estado_revision_y_sesion_se_revalidan(self):
        from dataclasses import replace
        token_luis = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        for punto, token in ((replace(self.punto, usuario_id=self.luis.id), self.token),
                             (replace(self.punto, revision=2), self.token),
                             (replace(self.punto, estado_vehiculo="en movimiento"), self.token),
                             (self.punto, token_luis)):
            with self.subTest(punto=punto, token=token), self.assertRaises(IntencionNoPermitida):
                self.completar(punto, token)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.completar()
        self.assertEqual(self.pendiente(), 1)
        self.assertIsNone(leer_punto(self.ruta, self.ana.id, "carrera-1"))

    def test_fallo_guardado_o_borrado_revierte_ambos(self):
        for sentencia in (
            "CREATE TRIGGER falla BEFORE INSERT ON carreras_recuperacion BEGIN SELECT RAISE(ABORT, 'error'); END",
            "CREATE TRIGGER falla BEFORE DELETE ON intenciones_operacion BEGIN SELECT RAISE(IGNORE); END",
        ):
            with self.subTest(sentencia=sentencia):
                with conectar(self.ruta) as conexion:
                    conexion.execute(sentencia)
                with self.assertRaises((sqlite3.IntegrityError, IntencionNoPermitida)):
                    self.completar()
                self.assertEqual(self.pendiente(), 1)
                self.assertIsNone(leer_punto(self.ruta, self.ana.id, "carrera-1"))
                with conectar(self.ruta) as conexion:
                    conexion.execute("DROP TRIGGER falla")

    def test_dos_confirmaciones_en_paralelo_reconocen_mismo_resultado(self):
        barrera = Barrier(2)
        def trabajo(_):
            barrera.wait(timeout=5)
            try:
                return self.completar()
            except IntencionNoPermitida:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(trabajo, range(2)))
        self.assertEqual(resultados, [self.punto, self.punto])
        self.assertEqual(self.pendiente(), 0)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, "carrera-1"), self.punto)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM recibos_operacion").fetchone()[0], 1)

    def test_commit_incierto_se_reintenta_de_forma_segura(self):
        for confirmar in (False, True):
            with self.subTest(confirmar=confirmar):
                if confirmar:
                    self.setUp()
                real_connect = sqlite3.connect
                class Proxy:
                    def __init__(self, real):
                        object.__setattr__(self, "real", real)
                    def __getattr__(self, nombre):
                        return getattr(self.real, nombre)
                    def __setattr__(self, nombre, valor):
                        setattr(self.real, nombre, valor)
                    def commit(self):
                        if confirmar:
                            self.real.commit()
                        raise OSError("Resultado incierto simulado")
                with patch("taxiroute.completar_intencion.sqlite3.connect",
                           side_effect=lambda *a, **kw: Proxy(real_connect(*a, **kw))):
                    with self.assertRaises(OSError):
                        self.completar()
                self.assertEqual(self.pendiente(), int(not confirmar))
                if confirmar:
                    self.assertEqual(leer_punto(self.ruta, self.ana.id, "carrera-1"), self.punto)
                    self.assertEqual(self.completar(), self.punto)
                else:
                    self.assertIsNone(leer_punto(self.ruta, self.ana.id, "carrera-1"))
                    self.assertEqual(self.completar(), self.punto)

    def test_recibo_rechaza_resultado_ajeno_o_diferente_y_uuid_no_se_reutiliza(self):
        from dataclasses import replace
        self.completar()
        with self.assertRaises(IntencionNoPermitida):
            self.completar(replace(self.punto, parado_ns=1))
        token_luis = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        with self.assertRaises(IntencionNoPermitida):
            self.completar(token=token_luis)
        with self.assertRaises(IntencionNoPermitida):
            registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                                accion="iniciar", revision=0)
        with self.assertRaises(IntencionNoPermitida):
            registrar_intencion(self.sesiones, token_luis, id_intencion=self.id,
                                accion="iniciar", revision=0)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, "carrera-1"), self.punto)

    def test_fallo_recibo_revierte_punto_y_conserva_intencion(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_recibo BEFORE INSERT ON recibos_operacion
                BEGIN SELECT RAISE(ABORT, 'fallo'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.completar()
        self.assertEqual(self.pendiente(), 1)
        self.assertIsNone(leer_punto(self.ruta, self.ana.id, "carrera-1"))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM recibos_operacion").fetchone()[0], 0)
