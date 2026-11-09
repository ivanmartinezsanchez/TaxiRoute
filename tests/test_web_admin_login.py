"""Acceso administrativo aislado: buzón y credenciales exclusivamente ficticios."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from flask import Flask, g
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion
from taxiroute.web_admin_login import (
    COOKIE_PIN, COOKIE_CAMBIO, RUTA_ADMIN, crear_acceso_administrador,
)


class PruebasAccesoAdministradorWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdministradorPrueba123!"
        cuenta = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin",
                              contrasena=self.clave, rol="administrador")
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
            c.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                      (cuenta.id, "prueba@example.invalid"))
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.buzon = []
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append(codigo), reloj=lambda: self.fecha)
        sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_acceso_administrador(self.pin))

        @app.get("/panel")
        @requerir_sesion(sesiones, roles={"administrador"})
        def panel():
            return g.identidad_taxiroute.rol
        self.cliente = app.test_client()
        self.origen = {"Origin": "https://localhost"}

    def post(self, ruta, datos, headers=None, base="https://localhost"):
        return self.cliente.post(ruta, base_url=base, json=datos,
                                 headers=self.origen if headers is None else headers)

    def iniciar(self, clave=None):
        return self.post(RUTA_ADMIN, {"usuario": "Admin",
                         "contrasena": self.clave if clave is None else clave})

    def verificar(self, codigo=None):
        return self.post(RUTA_ADMIN + "/pin",
                         {"codigo": self.buzon[-1] if codigo is None else codigo})

    def test_contrasena_y_pin_conceden_sesion(self):
        r = self.iniciar()
        self.assertEqual(r.json["paso"], "pin_pendiente")
        self.assertNotIn(self.buzon[-1], r.get_data(as_text=True))
        self.assertIsNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.assertEqual(self.cliente.get("/panel", base_url="https://localhost").status_code, 401)
        cabeceras = r.headers.getlist("Set-Cookie")
        self.assertTrue(any(COOKIE_PIN in h and "Secure" in h and "HttpOnly" in h
                            and "SameSite=Strict" in h for h in cabeceras))
        r = self.verificar()
        self.assertEqual(r.json["paso"], "acceso_completado")
        self.assertIsNone(self.cliente.get_cookie(COOKIE_PIN, path=RUTA_ADMIN))
        self.assertIsNotNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.assertEqual(self.cliente.get("/panel", base_url="https://localhost").status_code, 200)
        self.assertEqual(self.verificar().status_code, 401)

    def test_password_y_pin_erroneos_no_crean_sesion(self):
        self.assertEqual(self.iniciar("incorrecta").status_code, 401)
        self.assertEqual(self.buzon, [])
        self.fecha += timedelta(seconds=2)
        self.assertEqual(self.iniciar().status_code, 200)
        self.assertEqual(self.verificar("0000000").status_code, 401)
        self.assertIsNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.assertEqual(self.cliente.get("/panel", base_url="https://localhost").status_code, 401)

    def test_pin_caducado_y_otra_pestana_sin_cookie(self):
        self.iniciar()
        codigo = self.buzon[-1]
        self.cliente.delete_cookie(COOKIE_PIN, path=RUTA_ADMIN)
        self.assertEqual(self.verificar(codigo).status_code, 401)
        self.fecha += timedelta(minutes=1)
        self.iniciar()
        self.fecha += timedelta(minutes=5)
        self.assertEqual(self.verificar().status_code, 401)

    def test_cambio_temporal_no_abre_sesion_y_no_reutiliza_autorizacion(self):
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=1")
        self.iniciar()
        r = self.verificar()
        self.assertEqual(r.json["paso"], "cambio_pendiente")
        self.assertIsNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.assertIsNotNone(self.cliente.get_cookie(COOKIE_CAMBIO, path=RUTA_ADMIN))
        nueva = "NuevaTemporal456!"
        r = self.post(RUTA_ADMIN + "/cambio", {"contrasena_nueva": nueva})
        self.assertEqual(r.json["paso"], "volver_a_iniciar")
        self.assertIsNone(self.cliente.get_cookie(COOKIE_CAMBIO, path=RUTA_ADMIN))
        self.assertIsNone(self.cliente.get_cookie(NOMBRE_COOKIE))
        self.assertEqual(self.post(RUTA_ADMIN + "/cambio",
                         {"contrasena_nueva": "OtraTemporal789!"}).status_code, 400)

    def test_origen_ajeno_http_y_datos_invalidos(self):
        for ruta, datos in ((RUTA_ADMIN, {"usuario": "Admin", "contrasena": self.clave}),
                            (RUTA_ADMIN + "/pin", {"codigo": "1234567"}),
                            (RUTA_ADMIN + "/cambio", {"contrasena_nueva": "NuevaTemporal456!"})):
            self.assertEqual(self.post(ruta, datos, headers={}).status_code, 403)
            self.assertEqual(self.post(ruta, datos, headers={"Origin": "https://otro.example"}).status_code, 403)
            self.assertEqual(self.post(ruta, datos, base="http://localhost").status_code, 400)
        self.assertEqual(self.post(RUTA_ADMIN, {"usuario": "Admin", "contrasena": self.clave,
                                               "correo": "otro@example.invalid"}).status_code, 400)
        self.assertEqual(self.buzon, [])

    def test_no_cache_en_exito_y_error(self):
        self.assertEqual(self.iniciar("error").headers["Cache-Control"], "no-store")
        self.assertEqual(self.post(RUTA_ADMIN + "/pin", {"codigo": "1234567"}).headers["Cache-Control"], "no-store")
