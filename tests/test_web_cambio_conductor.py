"""Cambio web de contraseña de conductor con datos y cuentas ficticias."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from flask import Flask

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_cambio_conductor import crear_cambio_conductor
from taxiroute.web_login import crear_acceso_conductor


class PruebasCambioConductorWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.actual = "ActualFicticia123!"
        self.nueva = "NuevaFicticia456!"
        self.cuenta = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.actual)
        self.fecha = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_acceso_conductor(self.sesiones))
        app.register_blueprint(crear_cambio_conductor(self.ruta, reloj=lambda: self.fecha))
        self.cliente = app.test_client()
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def datos(self, **cambios):
        datos = {"usuario": "Ana", "contrasena_actual": self.actual, "contrasena_nueva": self.nueva}
        datos.update(cambios)
        return datos

    def cambio(self, datos=None, *, headers=None, base=None):
        return self.cliente.post("/acceso/conductor/cambio", base_url=base or self.url,
            headers=self.origen if headers is None else headers,
            json=self.datos() if datos is None else datos)

    def fila(self):
        with conectar(self.ruta) as conexion:
            return dict(conexion.execute("SELECT * FROM usuarios WHERE id=?", (self.cuenta.id,)).fetchone())

    def login(self, clave):
        return self.cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
            json={"usuario": "Ana", "contrasena": clave})

    def test_cambio_temporal_exige_login_posterior_y_persiste_hash(self):
        self.assertEqual(self.login(self.actual).status_code, 401)
        respuesta = self.cambio()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json, {"paso": "volver_a_iniciar"})
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", respuesta.headers)
        self.assertNotIn(self.actual, respuesta.get_data(as_text=True))
        self.assertNotIn(self.nueva, respuesta.get_data(as_text=True))
        fila = self.fila()
        self.assertEqual(fila["cambio_obligatorio"], 0)
        self.assertNotEqual(fila["hash_contrasena"], self.nueva)
        self.assertTrue(PasswordHasher().verify(fila["hash_contrasena"], self.nueva))
        self.assertEqual(self.login(self.nueva).status_code, 200)
        # El cambio persiste al volver a abrir SQLite.
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT version_credenciales FROM usuarios WHERE id=?", (self.cuenta.id,)).fetchone()[0], 2)

    def test_sesion_previa_se_invalida_al_cambiar(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.cuenta.id,))
        sesion = self.sesiones.iniciar_conductor("Ana", self.actual)
        self.assertIsNotNone(self.sesiones.consultar(sesion.token))
        self.assertEqual(self.cambio().status_code, 200)
        self.assertIsNone(self.sesiones.consultar(sesion.token))

    def test_rechazos_genericos_y_no_exponen_secretos(self):
        invalida = self.cambio(self.datos(contrasena_nueva="corta"))
        mismo = self.cambio(self.datos(contrasena_nueva=self.actual))
        desconocida = self.cambio(self.datos(usuario="Nadie"))
        incorrecta = self.cambio(self.datos(contrasena_actual="incorrecta"))
        for respuesta in (invalida, mismo, desconocida, incorrecta):
            self.assertEqual(respuesta.status_code, 400)
            self.assertEqual(respuesta.json, invalida.json)
            self.assertNotIn("Set-Cookie", respuesta.headers)
            self.assertNotIn(self.actual, respuesta.get_data(as_text=True))
            self.assertNotIn(self.nueva, respuesta.get_data(as_text=True))
        self.assertEqual(self.fila()["intentos_fallidos"], 1)
        self.assertEqual(self.fila()["cambio_obligatorio"], 1)

    def test_administrador_y_cuenta_deshabilitada_no_autorizados(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET rol='administrador' WHERE id=?", (self.cuenta.id,))
        admin = self.cambio()
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET rol='conductor', habilitado=0 WHERE id=?", (self.cuenta.id,))
        deshabilitada = self.cambio()
        self.assertEqual(admin.status_code, 400)
        self.assertEqual(admin.json, deshabilitada.json)
        self.assertEqual(self.fila()["version_credenciales"], 1)

    def test_http_origen_host_y_contenido_no_validos(self):
        self.assertEqual(self.cambio(base="http://localhost").status_code, 400)
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.cambio(headers=headers).status_code, 403)
        self.assertEqual(self.cliente.post("/acceso/conductor/cambio", base_url="https://otro.example",
            headers={"Origin": "https://otro.example"}, json=self.datos()).status_code, 400)
        self.assertEqual(self.cliente.post("/acceso/conductor/cambio", base_url=self.url,
            headers=self.origen, data="usuario=Ana").status_code, 415)
        self.assertEqual(self.cliente.post("/acceso/conductor/cambio?dato=1", base_url=self.url,
            headers=self.origen, json=self.datos()).status_code, 400)
        for datos in ({}, self.datos(rol="administrador"), self.datos(contrasena_nueva=None),
                      self.datos(usuario="a" * 41)):
            self.assertEqual(self.cambio(datos).status_code, 400)
        self.assertEqual(self.fila()["version_credenciales"], 1)

    def test_fallo_interno_no_muestra_detalles_ni_entrega_cookie(self):
        with patch("taxiroute.web_cambio_conductor.cambiar_contrasena", side_effect=RuntimeError("detalle privado")):
            respuesta = self.cambio()
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("detalle privado", respuesta.get_data(as_text=True))
        self.assertNotIn("Set-Cookie", respuesta.headers)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertEqual(self.fila()["version_credenciales"], 1)