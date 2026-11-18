"""Inicio de sesión web probado sin red y con cuentas temporales."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flask import Flask, g
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion
from taxiroute.web_login import crear_acceso_conductor


class PruebasLoginWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.clave = "ConductorPrueba123!"
        self.cuenta = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana",
                                  contrasena=self.clave)
        self.ruta = ruta
        with conectar(ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.servicio = ServicioSesiones(ruta, reloj=lambda: fecha)
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_acceso_conductor(self.servicio))

        @app.get("/protegida")
        @requerir_sesion(self.servicio, roles={"conductor"})
        def protegida():
            return g.identidad_taxiroute.usuario_id

        self.cliente = app.test_client()
        self.origen = {"Origin": "https://localhost"}

    def acceso(self, datos=None, **opciones):
        return self.cliente.post("/acceso/conductor", base_url="https://localhost",
            json={"usuario": "Ana", "contrasena": self.clave} if datos is None else datos,
            headers=opciones.get("headers", self.origen))

    def test_exito_configura_cookie_y_abre_ruta_protegida(self):
        respuesta = self.acceso()
        self.assertEqual(respuesta.status_code, 200)
        cookie = respuesta.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn(self.clave, cookie)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        resultado = self.cliente.get("/protegida", base_url="https://localhost")
        self.assertEqual(resultado.status_code, 200)
        self.assertEqual(resultado.get_data(as_text=True), self.cuenta.id)

    def test_credenciales_invalidas_no_entregan_cookie(self):
        for usuario, clave in (("Ana", "incorrecta"), ("NoExiste", self.clave)):
            r = self.acceso({"usuario": usuario, "contrasena": clave})
            self.assertEqual(r.status_code, 401)
            self.assertNotIn("Set-Cookie", r.headers)
            self.assertNotIn(clave, r.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/protegida", base_url="https://localhost").status_code, 401)

    def test_cuenta_temporal_y_administrador_no_inician_como_conductor(self):
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=1")
        self.assertEqual(self.acceso().status_code, 401)
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0, rol='administrador'")
        self.assertEqual(self.acceso().status_code, 401)

    def test_solicitud_insegura_y_origen_ajeno_rechazados(self):
        r = self.cliente.post("/acceso/conductor", base_url="http://localhost",
                              json={"usuario": "Ana", "contrasena": self.clave})
        self.assertEqual(r.status_code, 400)
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": "https://localhost", "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.acceso(headers=headers).status_code, 403)
        self.assertEqual(self.cliente.post("/acceso/conductor", base_url="https://localhost",
                         headers=self.origen, data="usuario=Ana").status_code, 415)

    def test_datos_no_validos_rechazados_y_host_externo(self):
        for datos in ({}, {"usuario": "Ana"}, {"usuario": 5, "contrasena": self.clave},
                      {"usuario": "Ana", "contrasena": self.clave, "rol": "administrador"}):
            self.assertEqual(self.acceso(datos).status_code, 400)
        r = self.cliente.post("/acceso/conductor", base_url="https://otro.example",
            headers={"Origin": "https://otro.example"},
            json={"usuario": "Ana", "contrasena": self.clave})
        self.assertEqual(r.status_code, 400)
