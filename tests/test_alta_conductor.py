"""Alta administrativa experimental con datos exclusivamente temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import _PROTECTOR, crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_alta_conductor import crear_alta_conductor_web


class PruebasAltaConductor(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        clave = "FicticiaLarga123!"
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin", contrasena=clave, rol="administrador")
        conductor = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        self.admin, self.conductor = admin.id, conductor.id
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
        app.register_blueprint(crear_alta_conductor_web(self.sesiones))
        self.admin_web, self.conductor_web, self.anonimo = app.test_client(), app.test_client(), app.test_client()
        self.admin_web.set_cookie(NOMBRE_COOKIE, self.admin_token, secure=True)
        self.conductor_web.set_cookie(NOMBRE_COOKIE, self.conductor_token, secure=True)
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}
        self.datos = {"usuario": "Luis", "nombre_visible": "Luis", "contrasena": "TemporalClave123!"}

    def post(self, cliente, datos=None, *, headers=None, base=None):
        return cliente.post("/api/administracion/conductores", base_url=base or self.url,
            headers=self.origen if headers is None else headers,
            json=self.datos if datos is None else datos)

    def nuevo(self):
        with conectar(self.ruta) as conexion:
            fila = conexion.execute("SELECT * FROM usuarios WHERE nombre_usuario='Luis'").fetchone()
            auditoria = conexion.execute("SELECT * FROM auditoria_cuentas WHERE accion='alta'").fetchall()
            return (dict(fila) if fila else None), [dict(x) for x in auditoria]

    def test_alta_crea_solo_conductor_con_hash_y_auditoria(self):
        respuesta = self.post(self.admin_web)
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertTrue(respuesta.json["cambio_obligatorio"])
        self.assertNotIn(self.datos["contrasena"], respuesta.get_data(as_text=True))
        fila, auditoria = self.nuevo()
        self.assertEqual((fila["rol"], fila["cambio_obligatorio"]), ("conductor", 1))
        self.assertNotEqual(fila["hash_contrasena"], self.datos["contrasena"])
        self.assertTrue(_PROTECTOR.verify(fila["hash_contrasena"], self.datos["contrasena"]))
        self.assertEqual(len(auditoria), 1)
        self.assertEqual((auditoria[0]["actor_id"], auditoria[0]["afectado_id"]), (self.admin, fila["id"]))
        self.assertIsNone(self.sesiones.iniciar_conductor("Luis", self.datos["contrasena"]))

    def test_anonimo_conductor_y_sesion_caducada_denegados(self):
        self.assertEqual(self.post(self.anonimo).status_code, 401)
        self.assertEqual(self.post(self.conductor_web).status_code, 403)
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.post(self.admin_web).status_code, 401)
        self.assertEqual(self.nuevo(), (None, []))

    def test_entrada_invalida_rol_extra_y_origen_rechazados(self):
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post(self.admin_web, headers=headers).status_code, 403)
        self.assertEqual(self.post(self.admin_web, base="http://localhost").status_code, 400)
        self.assertEqual(self.post(self.admin_web, {**self.datos, "rol": "administrador"}).status_code, 400)
        self.assertEqual(self.post(self.admin_web, {**self.datos, "contrasena": "facil"}).status_code, 400)
        self.assertEqual(self.post(self.admin_web, {**self.datos, "usuario": "a b"}).status_code, 400)
        self.assertEqual(self.nuevo(), (None, []))

    def test_duplicado_no_crea_otra_cuenta_ni_auditoria(self):
        self.assertEqual(self.post(self.admin_web).status_code, 201)
        respuesta = self.post(self.admin_web, {**self.datos, "usuario": "LUIS"})
        self.assertEqual(respuesta.status_code, 409)
        self.assertNotIn(self.datos["contrasena"], respuesta.get_data(as_text=True))
        self.assertEqual(len(self.nuevo()[1]), 1)

    def test_fallo_auditoria_revierte_alta(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_alta BEFORE INSERT ON auditoria_cuentas
                BEGIN SELECT RAISE(ABORT, 'error privado simulado'); END""")
        respuesta = self.post(self.admin_web)
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("error privado", respuesta.get_data(as_text=True))
        self.assertEqual(self.nuevo(), (None, []))

    def test_revocacion_durante_hash_impide_alta(self):
        hash_real = _PROTECTOR.hash
        def revocar_y_hashear(clave):
            protegido = hash_real(clave)
            self.sesiones.cerrar(self.admin_token)
            return protegido
        with patch("taxiroute.alta_conductor._PROTECTOR") as protector:
            protector.hash.side_effect = revocar_y_hashear
            respuesta = self.post(self.admin_web)
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.nuevo(), (None, []))