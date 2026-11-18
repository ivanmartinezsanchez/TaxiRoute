"""Puntos de control con tiempo y datos de prueba."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.control_periodico import ControlPeriodico
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.puntos_control import leer_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasControlPeriodico(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="FicticiaLarga123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="FicticiaLarga123!")
        with conectar(self.base) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(crear_carrera=lambda: Carrera(
            reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)), modo_durable_experimental=True)
        self.ids = {}
        for nombre, cuenta in (("Ana", self.ana), ("Luis", self.luis)):
            token = self.sesiones.iniciar_conductor(nombre, "FicticiaLarga123!").token
            op = str(uuid4())
            registrar_intencion(self.sesiones, token, id_intencion=op, accion="iniciar", revision=0)
            self.ids[cuenta.id] = self.flota.aplicar_intencion_experimental(self.sesiones, token, op).carrera_id
        self.control = ControlPeriodico(self.flota, self.base)

    def avanzar(self, segundos):
        self.fecha += timedelta(seconds=segundos)
        self.tiempo += segundos * 1_000_000_000

    def test_ciclo_guarda_cada_carrera_sin_cambiar_revision(self):
        self.avanzar(5)
        self.assertEqual(self.control.ciclo(), 0)
        for usuario in (self.ana.id, self.luis.id):
            punto = leer_punto(self.base, usuario, self.ids[usuario])
            self.assertEqual((punto.parado_ns, punto.revision), (5_000_000_000, 1))

    def test_fallo_una_carrera_no_impide_guardar_la_otra(self):
        from taxiroute.control_periodico import _guardar_punto_en_transaccion
        self.avanzar(5)
        def falla_ana(conexion, punto):
            if punto.usuario_id == self.ana.id:
                raise OSError("simulado")
            return _guardar_punto_en_transaccion(conexion, punto)
        with patch("taxiroute.control_periodico._guardar_punto_en_transaccion", side_effect=falla_ana):
            self.assertEqual(self.control.ciclo(), 1)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertFalse(self.flota.requiere_reconciliacion(self.luis.id))
        self.assertEqual(leer_punto(self.base, self.ana.id, self.ids[self.ana.id]).parado_ns, 0)
        self.assertEqual(leer_punto(self.base, self.luis.id, self.ids[self.luis.id]).parado_ns, 5_000_000_000)

    def test_base_ausente_no_se_recrea_y_bloquea_carreras(self):
        ausente = self.base.parent / "no-existe.sqlite3"
        control = ControlPeriodico(self.flota, ausente)
        self.assertEqual(control.ciclo(), 2)
        self.assertFalse(ausente.exists())
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertTrue(self.flota.requiere_reconciliacion(self.luis.id))

    def test_hilo_ejecuta_y_se_detiene(self):
        from threading import Event
        guardado = Event()
        self.avanzar(5)
        control = ControlPeriodico(self.flota, self.base, intervalo=0.05)
        guardar_real = control._guardar
        def guardar_y_notificar(usuario):
            guardar_real(usuario)
            guardado.set()
        control._guardar = guardar_y_notificar
        control.iniciar()
        self.addCleanup(control.detener)
        self.assertTrue(guardado.wait(3))
        control.detener()
        self.assertFalse(control._hilo.is_alive())
        self.assertEqual(leer_punto(self.base, self.ana.id, self.ids[self.ana.id]).parado_ns, 5_000_000_000)

    def test_si_falla_el_aviso_se_siguen_guardando_otras_carreras(self):
        from taxiroute.control_periodico import _guardar_punto_en_transaccion
        self.avanzar(5)
        def falla_ana(conexion, punto):
            if punto.usuario_id == self.ana.id:
                raise OSError("simulado")
            return _guardar_punto_en_transaccion(conexion, punto)
        def falla_aviso():
            raise OSError("salida no disponible")
        control = ControlPeriodico(self.flota, self.base, avisar=falla_aviso)
        with patch("taxiroute.control_periodico._guardar_punto_en_transaccion", side_effect=falla_ana):
            self.assertEqual(control.ciclo(), 1)
        self.assertEqual(leer_punto(self.base, self.luis.id, self.ids[self.luis.id]).parado_ns, 5_000_000_000)
