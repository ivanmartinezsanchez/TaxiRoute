"""Rutas aisladas de prueba: no cambian la aplicación web existente."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flask import Flask, g
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


class PruebasAccesoWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.clave = "ConductorPrueba123!"
        self.cuenta = crear_cuenta(self.ruta, nombre_usuario="Ana",
                                  nombre_visible="Ana", contrasena=self.clave)
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.cuenta.id,))
        self.servicio = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        app = Flask(__name__)
        app.testing = True

        @app.get("/conductor")
        @requerir_sesion(self.servicio, roles={"conductor", "administrador"})
        def conductor():
            return g.identidad_taxiroute.usuario_id

        @app.get("/administracion")
        @requerir_sesion(self.servicio, roles={"administrador"})
        def administracion():
            return "administración"

        self.cliente = app.test_client()

    def entrar(self):
        sesion = self.servicio.iniciar_conductor("Ana", self.clave)
        self.cliente.set_cookie(NOMBRE_COOKIE, sesion.token)
        return sesion.token

    def test_sin_sesion_o_token_inventado_denegado(self):
        self.assertEqual(self.cliente.get("/conductor").status_code, 401)
        self.cliente.set_cookie(NOMBRE_COOKIE, "x" * 43)
        self.assertEqual(self.cliente.get("/conductor").status_code, 401)

    def test_conductor_reconocido_y_administracion_denegada(self):
        self.entrar()
        respuesta = self.cliente.get("/conductor")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_data(as_text=True), self.cuenta.id)
        self.assertEqual(self.cliente.get("/administracion").status_code, 403)

    def test_sesion_cerrada_o_caducada_no_da_acceso(self):
        token = self.entrar()
        self.servicio.cerrar(token)
        self.assertEqual(self.cliente.get("/conductor").status_code, 401)
        self.entrar()
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.cliente.get("/conductor").status_code, 401)

    def test_consultas_no_renuevan_inactividad(self):
        self.entrar()
        self.fecha += timedelta(minutes=59)
        self.assertEqual(self.cliente.get("/conductor").status_code, 200)
        self.fecha += timedelta(minutes=1)
        self.assertEqual(self.cliente.get("/conductor").status_code, 401)

    def test_no_acepta_roles_invalidos_en_configuracion(self):
        for roles in (set(), {"superusuario"}):
            with self.assertRaises(ValueError):
                requerir_sesion(self.servicio, roles=roles)
