"""Sesión y preparación SQLite aisladas: sin finalización de la carrera en memoria."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final_con_sesion, preparar_punto_final_en_transaccion
from taxiroute.puntos_control import PuntoControl
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasFinalizacionConSesion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.ahora = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.ahora)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.punto = PuntoControl("carrera-1", self.ana.id, self.ahora, self.ahora + timedelta(seconds=10),
                                  "parado", 10_000_000_000, 0, "0.02", "0.05", 1)

    def fila(self):
        with conectar(self.ruta) as conexion:
            return conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()

    def test_sesion_correcta_prepara_y_confirma_la_misma_transaccion(self):
        observado = []
        def verificar(identidad, conexion):
            observado.append((identidad.usuario_id, conexion.in_transaction))
            return preparar_punto_final_en_transaccion(conexion, self.punto, self.punto.ultimo_control)
        resultado = self.sesiones.ejecutar_con_sesion_y_conexion(
            self.token, roles={"conductor"}, operacion=verificar)
        self.assertEqual(observado, [(self.ana.id, True)])
        self.assertEqual(str(resultado.importe), "0.20")
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")

    def test_sesion_no_puede_preparar_para_otra_cuenta(self):
        con_otro = replace(self.punto, usuario_id=self.luis.id)
        with self.assertRaises(SesionNoAutorizada):
            preparar_punto_final_con_sesion(self.sesiones, self.token, con_otro)
        self.assertIsNone(self.fila())

    def test_revocacion_o_caducidad_impiden_operacion(self):
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            preparar_punto_final_con_sesion(self.sesiones, self.token, self.punto)
        self.assertIsNone(self.fila())
        token_nuevo = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.ahora += timedelta(hours=1)
        with self.assertRaises(SesionNoAutorizada):
            preparar_punto_final_con_sesion(self.sesiones, token_nuevo, self.punto)
        self.assertIsNone(self.fila())

    def test_error_sql_revierte_toda_la_preparacion(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_final BEFORE UPDATE ON carreras_recuperacion
                WHEN NEW.estado_registro='guardado_pendiente'
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            preparar_punto_final_con_sesion(self.sesiones, self.token, self.punto)
        self.assertIsNone(self.fila())
        self.assertIsNotNone(self.sesiones.consultar(self.token))

    def test_revocacion_espera_a_una_preparacion_ya_autorizada(self):
        dentro = Event()
        liberar = Event()
        inicio_cierre = Event()
        cierre_completo = Event()
        def pausar(conexion, punto, fin):
            dentro.set()
            if not liberar.wait(3):
                raise TimeoutError("La prueba no liberó la preparación")
            return preparar_punto_final_en_transaccion(conexion, punto, fin)
        def cerrar():
            inicio_cierre.set()
            self.sesiones.cerrar(self.token)
            cierre_completo.set()
        with patch("taxiroute.finalizacion_pendiente.preparar_punto_final_en_transaccion", pausar):
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                futuro = ejecutor.submit(preparar_punto_final_con_sesion, self.sesiones, self.token, self.punto)
                self.assertTrue(dentro.wait(3))
                futuro_cierre = ejecutor.submit(cerrar)
                try:
                    self.assertTrue(inicio_cierre.wait(3))
                    self.assertFalse(cierre_completo.wait(0.15))
                finally:
                    liberar.set()
                self.assertEqual(str(futuro.result(timeout=4).importe), "0.20")
                futuro_cierre.result(timeout=4)
        self.assertTrue(cierre_completo.is_set())
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")
        with self.assertRaises(SesionNoAutorizada):
            preparar_punto_final_con_sesion(self.sesiones, self.token, self.punto)