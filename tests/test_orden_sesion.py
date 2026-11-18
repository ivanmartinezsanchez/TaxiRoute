"""Orden entre una acción en memoria y la revocación de su sesión."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasOrdenSesion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(ruta)
        cuenta = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana",
                              contrasena="ConductorPrueba123!")
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
        self.cuenta = cuenta
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: datetime(2026, 11, 8, tzinfo=timezone.utc))
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras())

    def ejecutar(self, operacion):
        return self.sesiones.ejecutar_con_sesion_vigente(
            self.token, roles={"conductor"}, operacion=operacion)

    def test_revocacion_previa_impide_la_accion(self):
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.ejecutar(lambda identidad: self.flota.ejecutar(identidad.usuario_id, "iniciar"))
        self.assertIsNone(self.flota.consultar(self.cuenta.id))

    def test_accion_iniciada_termina_antes_de_completar_la_revocacion(self):
        dentro_accion = Event()
        liberar_accion = Event()
        intento_cierre = Event()
        cierre_terminado = Event()
        def operacion(identidad):
            dentro_accion.set()
            if not liberar_accion.wait(3):
                raise TimeoutError("La prueba no liberó la acción")
            return self.flota.ejecutar(identidad.usuario_id, "iniciar")
        def cierre():
            intento_cierre.set()
            self.sesiones.cerrar(self.token)
            cierre_terminado.set()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            futuro_accion = ejecutor.submit(self.ejecutar, operacion)
            self.assertTrue(dentro_accion.wait(3))
            futuro_cierre = ejecutor.submit(cierre)
            try:
                self.assertTrue(intento_cierre.wait(3))
                self.assertFalse(cierre_terminado.wait(0.15))
            finally:
                liberar_accion.set()
            self.assertTrue(futuro_accion.result(timeout=3).activa)
            futuro_cierre.result(timeout=3)
        self.assertTrue(cierre_terminado.is_set())
        self.assertTrue(self.flota.consultar(self.cuenta.id).activa)
        with self.assertRaises(SesionNoAutorizada):
            self.ejecutar(lambda identidad: self.flota.ejecutar(identidad.usuario_id, "finalizar"))
        self.assertTrue(self.flota.consultar(self.cuenta.id).activa)