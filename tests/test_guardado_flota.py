"""Guardado coordinado de capturas de flota con datos temporales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.puntos_control import PuntoControlInvalido, guardar_punto, leer_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasGuardadoFlota(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.tiempo = 0
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)))

    def avanzar(self, segundos):
        self.tiempo += segundos * 1_000_000_000
        self.fecha += timedelta(seconds=segundos)

    def test_guarda_capturas_por_usuario_y_rechaza_una_antigua(self):
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.flota.ejecutar(self.luis.id, "iniciar")
        self.avanzar(5)
        primera = self.flota.guardar_captura(self.ruta, self.ana.id)
        luis = self.flota.guardar_captura(self.ruta, self.luis.id)
        self.flota.ejecutar(self.ana.id, "mover")
        self.avanzar(5)
        ultima = self.flota.guardar_captura(self.ruta, self.ana.id)
        self.assertEqual((ultima.parado_ns, ultima.movimiento_ns, ultima.revision),
                         (5_000_000_000, 5_000_000_000, 2))
        self.assertEqual(leer_punto(self.ruta, self.ana.id, ultima.carrera_id), ultima)
        self.assertEqual(leer_punto(self.ruta, self.luis.id, luis.carrera_id), luis)
        self.assertIsNone(leer_punto(self.ruta, self.luis.id, ultima.carrera_id))
        with self.assertRaises(PuntoControlInvalido):
            guardar_punto(self.ruta, primera)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, ultima.carrera_id), ultima)

    def test_fallo_no_altera_carrera_ni_ultimo_punto(self):
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.avanzar(5)
        anterior = self.flota.guardar_captura(self.ruta, self.ana.id)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_guardado BEFORE UPDATE ON carreras_recuperacion
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        self.avanzar(5)
        with self.assertRaises(sqlite3.IntegrityError):
            self.flota.guardar_captura(self.ruta, self.ana.id)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, anterior.carrera_id), anterior)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)
        self.assertEqual(self.flota.consultar(self.ana.id).importe, self.flota.capturar(self.ana.id).carrera.tarifa_parado * 10)

    def test_accion_con_sesion_y_guardado_no_invierten_bloqueos(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.ana.id,))
        sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        token = sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.avanzar(5)
        dentro_transaccion = Event()
        liberar = Event()
        inicio_guardado = Event()
        def operacion(identidad):
            dentro_transaccion.set()
            if not liberar.wait(3):
                raise TimeoutError("La prueba no liberó la acción")
            return self.flota.ejecutar(identidad.usuario_id, "mover")
        def accion():
            return sesiones.ejecutar_con_sesion_vigente(token, roles={"conductor"}, operacion=operacion)
        def guardar():
            inicio_guardado.set()
            return self.flota.guardar_captura(self.ruta, self.ana.id)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            futuro_accion = ejecutor.submit(accion)
            self.assertTrue(dentro_transaccion.wait(3))
            futuro_guardado = ejecutor.submit(guardar)
            try:
                self.assertTrue(inicio_guardado.wait(3))
            finally:
                liberar.set()
            self.assertEqual(futuro_accion.result(timeout=4).estado, "en movimiento")
            punto = futuro_guardado.result(timeout=4)
        self.assertEqual((punto.estado_vehiculo, punto.revision), ("en movimiento", 2))
        self.assertEqual(leer_punto(self.ruta, self.ana.id, punto.carrera_id), punto)
    def test_guardado_primero_termina_y_accion_autenticada_continua(self):
        from taxiroute.puntos_control import _guardar_punto_en_transaccion
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.ana.id,))
        sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        token = sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.avanzar(5)
        dentro_guardado = Event()
        liberar = Event()
        empezo_accion = Event()
        def guardar_con_pausa(conexion, punto):
            dentro_guardado.set()
            if not liberar.wait(3):
                raise TimeoutError("La prueba no liberó el guardado")
            return _guardar_punto_en_transaccion(conexion, punto)
        def accion():
            empezo_accion.set()
            return sesiones.ejecutar_con_sesion_vigente(token, roles={"conductor"},
                operacion=lambda identidad: self.flota.ejecutar(identidad.usuario_id, "mover"))
        with patch("taxiroute.puntos_control._guardar_punto_en_transaccion", guardar_con_pausa):
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                futuro_guardado = ejecutor.submit(self.flota.guardar_captura, self.ruta, self.ana.id)
                self.assertTrue(dentro_guardado.wait(3))
                futuro_accion = ejecutor.submit(accion)
                try:
                    self.assertTrue(empezo_accion.wait(3))
                finally:
                    liberar.set()
                punto = futuro_guardado.result(timeout=4)
                resultado = futuro_accion.result(timeout=4)
        self.assertEqual((punto.estado_vehiculo, punto.revision), ("parado", 1))
        self.assertEqual((resultado.estado, resultado.revision), ("en movimiento", 2))

    def test_limitacion_finalizacion_no_actualiza_el_punto_guardado(self):
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.avanzar(5)
        anterior = self.flota.guardar_captura(self.ruta, self.ana.id)
        self.flota.ejecutar(self.ana.id, "finalizar")
        self.assertEqual(leer_punto(self.ruta, self.ana.id, anterior.carrera_id), anterior)
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.avanzar(5)
        with self.assertRaises(sqlite3.IntegrityError):
            self.flota.guardar_captura(self.ruta, self.ana.id)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)
    def test_sin_carrera_no_escribe_y_finalizacion_no_crea_otra_captura(self):
        self.assertIsNone(self.flota.guardar_captura(self.ruta, self.ana.id))
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.flota.ejecutar(self.ana.id, "finalizar")
        self.assertIsNone(self.flota.guardar_captura(self.ruta, self.ana.id))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 0)