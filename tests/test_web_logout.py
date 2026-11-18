"""Cierre web aislado con datos y conexiones simulados."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion
from taxiroute.web_login import crear_acceso_conductor


class PruebasCierreWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(ruta)
        crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana",
                     contrasena="ConductorPrueba123!")
        with conectar(ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: fecha)
        self.tiempo = [0]
        self.carreras = ServicioCarreras(lambda: Carrera(reloj=lambda: self.tiempo[0]))
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_acceso_conductor(self.sesiones))

        @app.get("/protegida")
        @requerir_sesion(self.sesiones, roles={"conductor"})
        def protegida():
            return "permitido"

        self.cliente = app.test_client()
        self.origen = {"Origin": "https://localhost"}

    def entrar(self):
        r = self.cliente.post("/acceso/conductor", base_url="https://localhost",
            headers=self.origen, json={"usuario": "Ana", "contrasena": "ConductorPrueba123!"})
        self.assertEqual(r.status_code, 200)
        return self.cliente.get_cookie(NOMBRE_COOKIE).value

    def cerrar(self, headers=None, base_url="https://localhost"):
        return self.cliente.post("/acceso/cerrar", base_url=base_url,
                                 headers=self.origen if headers is None else headers)

    def test_revoca_cookie_y_no_permite_reutilizar_token(self):
        token = self.entrar()
        r = self.cerrar()
        self.assertEqual(r.status_code, 200)
        borrado = r.headers["Set-Cookie"]
        for valor in ("Max-Age=0", "Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(valor, borrado)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIsNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.cliente.set_cookie(NOMBRE_COOKIE, token)
        self.assertEqual(self.cliente.get("/protegida", base_url="https://localhost").status_code, 401)

    def test_repetir_cierre_es_seguro(self):
        self.entrar()
        self.assertEqual(self.cerrar().status_code, 200)
        self.assertEqual(self.cerrar().status_code, 200)

    def test_rechaza_origen_ajeno_y_http_sin_revocar(self):
        token = self.entrar()
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": "https://localhost", "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.cerrar(headers=headers).status_code, 403)
        self.assertEqual(self.cerrar(base_url="http://localhost").status_code, 400)
        self.assertIsNotNone(self.sesiones.consultar(token))

    def test_cerrar_no_finaliza_carrera(self):
        self.entrar()
        self.carreras.ejecutar("iniciar")
        self.assertEqual(self.cerrar().status_code, 200)
        self.tiempo[0] = 10_000_000_000
        self.assertIsNotNone(self.carreras.carrera)
        self.assertEqual(str(self.carreras.carrera.importe_actual()), "0.20")

    def test_fallo_base_no_indica_cierre_ni_borra_cookie(self):
        token = self.entrar()
        with patch.object(self.sesiones, "cerrar", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                self.cerrar()
        self.assertEqual(self.cliente.get_cookie(NOMBRE_COOKIE).value, token)
        self.assertIsNotNone(self.sesiones.consultar(token))
