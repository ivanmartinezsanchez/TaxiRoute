"""Administración web aislada con usuarios, sesiones y correo ficticios."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.servicio import ServicioCarreras
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_administracion import crear_administracion_web


class PruebasAdministracionWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        clave = "FicticiaLarga123!"
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Administración", contrasena=clave, rol="administrador")
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
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras())
        app.register_blueprint(crear_administracion_web(self.sesiones, self.flota))
        self.admin_web, self.conductor_web, self.anonimo = app.test_client(), app.test_client(), app.test_client()
        self.admin_web.set_cookie(NOMBRE_COOKIE, self.admin_token, secure=True)
        self.conductor_web.set_cookie(NOMBRE_COOKIE, self.conductor_token, secure=True)
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}
        self.lista = "/api/administracion/conductores"
        self.accion = self.lista + "/" + self.conductor + "/accion"

    def post(self, cliente, accion, *, ruta=None, headers=None, base=None):
        return cliente.post(ruta or self.accion, base_url=base or self.url,
                            headers=self.origen if headers is None else headers,
                            json={"accion": accion})

    def datos(self):
        with conectar(self.ruta) as conexion:
            cuenta = dict(conexion.execute("SELECT * FROM usuarios WHERE id=?", (self.conductor,)).fetchone())
            auditoria = [fila["accion"] for fila in conexion.execute("SELECT accion FROM auditoria_cuentas ORDER BY id")]
            return cuenta, auditoria

    def test_lista_solo_conductores_y_campos_publicos(self):
        respuesta = self.admin_web.get(self.lista, base_url=self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertEqual(respuesta.json["conductores"], [{"id": self.conductor, "usuario": "Ana",
            "nombre_visible": "Ana", "habilitado": True, "bloqueado": False}])
        contenido = respuesta.get_data(as_text=True)
        for sensible in ("hash_contrasena", "correo_verificacion", "FicticiaLarga123!", self.admin_token):
            self.assertNotIn(sensible, contenido)

    def test_sin_sesion_conductor_y_caducada_no_pueden_administrar(self):
        self.assertEqual(self.anonimo.get(self.lista, base_url=self.url).status_code, 401)
        self.assertEqual(self.conductor_web.get(self.lista, base_url=self.url).status_code, 403)
        self.assertEqual(self.post(self.conductor_web, "desactivar").status_code, 403)
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.admin_web.get(self.lista, base_url=self.url).status_code, 401)
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 401)
        cuenta, auditoria = self.datos()
        self.assertEqual(cuenta["habilitado"], 1)
        self.assertEqual(auditoria, [])

    def test_origen_https_y_datos_extras_denegados_sin_cambios(self):
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post(self.admin_web, "desactivar", headers=headers).status_code, 403)
        self.assertEqual(self.post(self.admin_web, "desactivar", base="http://localhost").status_code, 400)
        self.assertEqual(self.post(self.admin_web, "borrar").status_code, 400)
        extras = self.admin_web.post(self.accion, base_url=self.url, headers=self.origen,
                                    json={"accion": "desactivar", "rol": "administrador"})
        self.assertEqual(extras.status_code, 400)
        self.assertEqual(self.admin_web.post(self.accion, base_url=self.url, headers=self.origen,
                                             data="accion=desactivar").status_code, 415)
        self.assertEqual(self.admin_web.get(self.lista + "?rol=administrador", base_url=self.url).status_code, 400)
        self.assertEqual(self.datos()[1], [])

    def test_no_administra_administradores_ni_cuentas_inexistentes(self):
        for identificador in (self.admin, "no-existe"):
            ruta = self.lista + "/" + identificador + "/accion"
            self.assertEqual(self.post(self.admin_web, "desactivar", ruta=ruta).status_code, 403)
        self.assertEqual(self.datos()[1], [])

    def test_desactivar_reactivar_y_revocar_sesiones(self):
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 200)
        self.assertIsNone(self.sesiones.consultar(self.conductor_token))
        self.assertEqual(self.post(self.admin_web, "reactivar").status_code, 200)
        cuenta, auditoria = self.datos()
        self.assertEqual(cuenta["habilitado"], 1)
        self.assertEqual(auditoria, ["desactivar", "reactivar"])
        self.assertIsNone(self.sesiones.consultar(self.conductor_token))

    def test_desbloquear_y_reactivar_son_independientes(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=0, intentos_fallidos=15, bloqueado_en=? WHERE id=?",
                             (self.fecha.isoformat(), self.conductor))
        self.assertEqual(self.post(self.admin_web, "reactivar").status_code, 200)
        self.assertEqual(self.datos()[0]["habilitado"], 1)
        self.assertIsNotNone(self.datos()[0]["bloqueado_en"])
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 200)
        self.assertEqual(self.post(self.admin_web, "desbloquear").status_code, 200)
        cuenta, _ = self.datos()
        self.assertEqual((cuenta["habilitado"], cuenta["intentos_fallidos"]), (0, 0))
        self.assertIsNone(cuenta["bloqueado_en"])

    def test_accion_admin_renueva_inactividad_lista_no(self):
        self.fecha += timedelta(minutes=30)
        self.assertEqual(self.post(self.admin_web, "revocar").status_code, 200)
        self.fecha += timedelta(minutes=31)
        self.assertEqual(self.admin_web.get(self.lista, base_url=self.url).status_code, 200)
        self.fecha += timedelta(minutes=29)
        self.assertEqual(self.admin_web.get(self.lista, base_url=self.url).status_code, 401)
    def test_revocar_no_desactiva_y_fallo_auditoria_revierte(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_auditoria BEFORE INSERT ON auditoria_cuentas
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 503)
        self.assertEqual(self.datos()[0]["habilitado"], 1)
        self.assertIsNotNone(self.sesiones.consultar(self.conductor_token))
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP TRIGGER fallo_auditoria")
        self.assertEqual(self.post(self.admin_web, "revocar").status_code, 200)
        self.assertEqual(self.datos()[0]["habilitado"], 1)
        self.assertIsNone(self.sesiones.consultar(self.conductor_token))
    def test_desactivar_con_carrera_activa_informa_y_no_revoca(self):
        self.sesiones.ejecutar_con_sesion_vigente(self.conductor_token, roles={"conductor"},
            operacion=lambda identidad: self.flota.ejecutar_controlado(identidad.usuario_id, "iniciar", 0))
        respuesta = self.post(self.admin_web, "desactivar")
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("Finaliza o revisa", respuesta.json["error"])
        self.assertEqual(self.datos()[0]["habilitado"], 1)
        self.assertIsNotNone(self.sesiones.consultar(self.conductor_token))

    def test_despues_de_finalizar_se_puede_desactivar(self):
        def actuar(comando, revision):
            return self.sesiones.ejecutar_con_sesion_vigente(self.conductor_token, roles={"conductor"},
                operacion=lambda identidad: self.flota.ejecutar_controlado(identidad.usuario_id, comando, revision))
        actuar("iniciar", 0)
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 409)
        actuar("finalizar", 1)
        self.assertEqual(self.post(self.admin_web, "desactivar").status_code, 200)
        self.assertEqual(self.datos()[0]["habilitado"], 0)
        self.assertIsNone(self.sesiones.consultar(self.conductor_token))
