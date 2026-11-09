"""Pruebas experimentales con cuentas y bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base, VERSION_ESQUEMA
from taxiroute.cuentas import crear_cuenta
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasIntenciones(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.ruta = Path(carpeta.name) / "prueba.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token

    def registrar(self, id_intencion, accion="iniciar", revision=0, carrera_id=None, token=None):
        return registrar_intencion(self.sesiones, token or self.token, id_intencion=id_intencion,
                                  accion=accion, revision=revision, carrera_id=carrera_id)

    def test_reintento_idempotente_y_otra_intencion_bloqueada(self):
        identificador = str(uuid4())
        primero = self.registrar(identificador)
        self.assertEqual(self.registrar(identificador), primero)
        with self.assertRaises(IntencionNoPermitida):
            self.registrar(str(uuid4()))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.registrar(identificador)

    def test_revision_estado_y_propiedad_de_carrera(self):
        punto = PuntoControl("carrera-1", self.ana.id, self.fecha,
                             self.fecha + timedelta(seconds=1), "parado", 1, 0, "0.02", "0.05", 1)
        guardar_punto(self.ruta, punto)
        for accion, revision, carrera in (("mover", 0, "carrera-1"), ("parar", 1, "carrera-1"),
                                           ("mover", 1, "otra")):
            with self.subTest(accion=accion, revision=revision), self.assertRaises(IntencionNoPermitida):
                self.registrar(str(uuid4()), accion, revision, carrera)
        self.assertEqual(self.registrar(str(uuid4()), "mover", 1, "carrera-1").usuario_id, self.ana.id)

    def test_sesion_ajena_y_caducada_no_pueden_reutilizar_id(self):
        identificador = str(uuid4())
        self.registrar(identificador)
        token_luis = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        with self.assertRaises(IntencionNoPermitida):
            self.registrar(identificador, token=token_luis)
        self.fecha += timedelta(hours=1)
        with self.assertRaises(SesionNoAutorizada):
            self.registrar(identificador)

    def test_trigger_que_ignora_insert_no_devuelve_exito(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER ignora_intencion BEFORE INSERT ON intenciones_operacion
                BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaises(IntencionNoPermitida):
            self.registrar(str(uuid4()))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 0)

    def test_dos_intenciones_concurrentes_solo_una_se_guarda(self):
        barrera = Barrier(2)
        def trabajo(_):
            barrera.wait(timeout=5)
            try:
                return self.registrar(str(uuid4()))
            except IntencionNoPermitida:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(trabajo, range(2)))
        self.assertEqual(sum(x is not None for x in resultados), 1)

    def test_error_de_commit_antes_y_despues_permite_reintento(self):
        for confirmar in (False, True):
            with self.subTest(confirmar=confirmar):
                if confirmar:
                    with conectar(self.ruta) as conexion:
                        conexion.execute("DELETE FROM intenciones_operacion")
                identificador = str(uuid4())
                conectar_real = sqlite3.connect
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
                        raise OSError("Fallo de confirmación simulado")
                with patch("taxiroute.intenciones_operacion.sqlite3.connect",
                           side_effect=lambda *a, **kw: Proxy(conectar_real(*a, **kw))):
                    with self.assertRaises(OSError):
                        self.registrar(identificador)
                with conectar(self.ruta) as conexion:
                    cantidad = conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0]
                self.assertEqual(cantidad, int(confirmar))
                self.assertEqual(self.registrar(identificador).id, identificador)

    def test_migracion_v8_reversible_si_falla_y_conserva_cuentas(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TABLE recibos_operacion")
            conexion.execute("DROP TABLE intenciones_operacion")
            conexion.execute("PRAGMA user_version=8")
        with patch("taxiroute.base_cuentas.MIGRACION_INTENCIONES_V9",
                   ("CREATE TABLE temporal_prueba (x INTEGER)", "SQL INVALIDO")):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='temporal_prueba'").fetchone())
        inicializar_base(self.ruta)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 2)
