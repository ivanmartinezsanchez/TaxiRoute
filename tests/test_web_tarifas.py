"""Tarifas web con sesiones y archivos temporales."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas, guardar_tarifas
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_tarifas import crear_tarifas_web


class PruebasTarifasWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.ruta = carpeta / "cuentas.sqlite3"
        self.tarifas = carpeta / "tarifas.json"
        inicializar_base(self.ruta)
        guardar_tarifas(self.tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
        clave = "FicticiaLarga123!"
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin", contrasena=clave, rol="administrador")
        conductor = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (admin.id, "demo@example.invalid"))
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
                         enviar=lambda correo, codigo: buzon.append(codigo), reloj=lambda: self.fecha)
        desafio = pin.solicitar("Admin", clave)
        self.admin_token = pin.verificar(desafio, buzon[-1]).sesion.token
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.conductor_token = self.sesiones.iniciar_conductor("Ana", clave).token
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_tarifas_web(self.sesiones, self.tarifas))
        self.admin_web, self.conductor_web, self.anonimo = app.test_client(), app.test_client(), app.test_client()
        self.admin_web.set_cookie(NOMBRE_COOKIE, self.admin_token, secure=True)
        self.conductor_web.set_cookie(NOMBRE_COOKIE, self.conductor_token, secure=True)
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}
        self.datos = {"parado": "0.03", "en_movimiento": "0.06"}

    def post(self, cliente, datos=None, *, headers=None, base=None):
        return cliente.post("/api/tarifas", base_url=base or self.url,
            headers=self.origen if headers is None else headers,
            json=self.datos if datos is None else datos)

    def test_lectura_autorizada_y_escritura_solo_administrador(self):
        self.assertEqual(self.anonimo.get("/api/tarifas", base_url=self.url).status_code, 401)
        self.assertEqual(self.conductor_web.get("/api/tarifas", base_url=self.url).json["tarifas"],
                         {"parado": "0.02", "en movimiento": "0.05"})
        self.assertEqual(self.post(self.conductor_web).status_code, 403)
        respuesta = self.post(self.admin_web)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertEqual(self.conductor_web.get("/api/tarifas", base_url=self.url).json["tarifas"],
                         {"parado": "0.03", "en movimiento": "0.06"})

    def test_cambio_no_altera_carrera_activa_y_aplica_a_la_siguiente(self):
        tiempo = [0]
        activa = Carrera(reloj=lambda: tiempo[0], tarifas=cargar_tarifas(self.tarifas))
        tiempo[0] = 10_000_000_000
        self.assertEqual(self.post(self.admin_web).status_code, 200)
        self.assertEqual(activa.finalizar(), Decimal("0.20"))
        siguiente = Carrera(reloj=lambda: tiempo[0], tarifas=cargar_tarifas(self.tarifas))
        tiempo[0] = 20_000_000_000
        self.assertEqual(siguiente.finalizar(), Decimal("0.30"))

    def test_origen_http_y_estructura_no_valida_no_guardan(self):
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post(self.admin_web, headers=headers).status_code, 403)
        self.assertEqual(self.post(self.admin_web, base="http://localhost").status_code, 400)
        for datos in ({}, {"parado": "0.03"}, {**self.datos, "rol": "administrador"},
                      {"parado": -1, "en_movimiento": "0.05"},
                      {"parado": "NaN", "en_movimiento": "0.05"}):
            self.assertEqual(self.post(self.admin_web, datos).status_code, 400)
        self.assertEqual(self.admin_web.post("/api/tarifas", base_url=self.url, headers=self.origen,
                                             data="parado=0.03").status_code, 415)
        self.assertEqual(self.admin_web.get("/api/tarifas?usuario=otro", base_url=self.url).status_code, 400)
        self.assertEqual(self.conductor_web.get("/api/tarifas", base_url=self.url).json["tarifas"]["parado"], "0.02")

    def test_sesion_caducada_o_revocada_no_puede_guardar(self):
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.post(self.admin_web).status_code, 401)
        self.assertEqual(self.admin_web.get("/api/tarifas", base_url=self.url).status_code, 401)
        self.assertEqual(cargar_tarifas(self.tarifas)[next(iter(cargar_tarifas(self.tarifas)))], Decimal("0.02"))

    def test_fallo_guardado_es_generico_y_conserva_tarifas(self):
        with patch("taxiroute.web_tarifas.guardar_tarifas", side_effect=OSError("ruta privada simulada")):
            respuesta = self.post(self.admin_web)
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("ruta privada", respuesta.get_data(as_text=True))
        self.assertEqual(self.conductor_web.get("/api/tarifas", base_url=self.url).json["tarifas"]["parado"], "0.02")