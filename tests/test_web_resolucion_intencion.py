"""Rutas experimentales probadas con cuentas y archivos temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.pin import ServicioPIN
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_resolucion_intencion import crear_web_resolucion_intencion


class PruebasWebResolverIntencion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        self.admin = crear_cuenta(self.base, nombre_usuario="Admin", nombre_visible="Admin", contrasena="AdminPrueba123!", rol="administrador")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (self.admin.id, "demo@example.invalid"))
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.tokens = [self.sesiones.iniciar_conductor(nombre, "Conductor123!").token for nombre in ("Ana", "Luis")]
        buzon = []
        pin = ServicioPIN(self.base, clave_hmac=b"x" * 32, reloj=lambda: self.fecha,
                          enviar=lambda _, codigo: buzon.append(codigo))
        desafio = pin.solicitar("Admin", "AdminPrueba123!")
        self.tokens.append(pin.verificar(desafio, buzon[-1]).sesion.token)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(), usuarios_pendientes=[self.ana.id],
                                    modo_durable_experimental=True, revisiones_iniciales={self.ana.id: 0})
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.config["DEMO_PERSISTENTE"] = True
        self.app.register_blueprint(crear_web_resolucion_intencion(self.sesiones, self.flota))
        self.clientes = [self.app.test_client() for _ in self.tokens]
        for cliente, token in zip(self.clientes, self.tokens):
            cliente.set_cookie(NOMBRE_COOKIE, token, secure=True)
        self.anonimo = self.app.test_client()
        self.url, self.headers = "https://localhost", {"Origin": "https://localhost"}
        self.ruta = f"/api/recuperacion/administracion/{self.ana.id}/intencion"
        self.propia = "/api/recuperacion/intencion/confirmar"
        self.id = str(uuid4())

    def get(self, cliente, ruta=None, base=None):
        return cliente.get(ruta or self.ruta, base_url=base or self.url)

    def post(self, cliente, ruta, datos, headers=None, base=None):
        return cliente.post(ruta, base_url=base or self.url, headers=self.headers if headers is None else headers, json=datos)

    def iniciar(self):
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=self.id, accion="iniciar", revision=0)

    def test_inicio_solo_admin_ve_y_confirma(self):
        self.iniciar()
        self.assertEqual(self.get(self.anonimo).status_code, 401)
        self.assertEqual(self.get(self.clientes[0]).status_code, 403)
        resultado = self.get(self.clientes[2])
        self.assertEqual(resultado.status_code, 200)
        self.assertIsNone(resultado.json["pendiente"])
        self.assertEqual(resultado.json["intencion"]["accion"], "iniciar")
        self.assertNotIn(self.id, resultado.get_data(as_text=True))
        huella = resultado.json["intencion"]["huella"]
        datos = {"huella": huella, "confirmar": True}
        self.assertEqual(self.post(self.clientes[0], self.propia, datos).status_code, 403)
        self.assertEqual(self.post(self.clientes[1], self.ruta + "/resolver", datos).status_code, 403)
        respuesta = self.post(self.clientes[2], self.ruta + "/resolver", datos)
        self.assertEqual((respuesta.status_code, respuesta.json["resultado"]), (200, "inicio_revisado"))
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.post(self.clientes[2], self.ruta + "/resolver", datos).status_code, 200)

    def test_movimiento_conductor_revisa_su_propia_accion(self):
        inicio = self.fecha - timedelta(hours=4)
        punto = PuntoControl("carrera-ficticia", self.ana.id, inicio, inicio + timedelta(seconds=10), "parado",
                             10_000_000_000, 0, "0.02", "0.05", 1)
        guardar_punto(self.base, punto)
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=self.id, accion="mover", revision=1, carrera_id=punto.carrera_id)
        self.flota._revisiones[self.ana.id] = 1
        diagnostico = self.get(self.clientes[2]).json
        self.assertEqual(diagnostico["pendiente"]["importe_recuperado"], "0.20")
        self.assertEqual(diagnostico["intencion"]["accion"], "mover")
        respuesta = self.post(self.clientes[0], self.propia, {"huella": diagnostico["intencion"]["huella"], "confirmar": True})
        self.assertEqual((respuesta.status_code, respuesta.json["resultado"]), (200, "carrera_pendiente"))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT estado_registro FROM carreras_recuperacion").fetchone()[0], "revision_pendiente")

    def test_peticion_insegura_origen_y_cuerpo_incorrectos(self):
        self.iniciar()
        datos = {"huella": self.get(self.clientes[2]).json["intencion"]["huella"], "confirmar": True}
        self.assertEqual(self.get(self.clientes[2], base="http://localhost").status_code, 400)
        self.assertEqual(self.get(self.clientes[2], self.ruta + "?otro=1").status_code, 400)
        self.assertEqual(self.post(self.clientes[2], self.ruta + "/resolver", datos, headers={}).status_code, 403)
        self.assertEqual(self.post(self.clientes[2], self.ruta + "/resolver", datos, headers={"Origin": self.url, "Sec-Fetch-Site": "cross-site"}).status_code, 403)
        for mal in ({"huella": datos["huella"], "confirmar": False}, {**datos, "usuario": self.ana.id}, {"huella": "x" * 64, "confirmar": True}):
            self.assertEqual(self.post(self.clientes[2], self.ruta + "/resolver", mal).status_code, 400)
        self.assertEqual(self.post(self.clientes[2], self.ruta + "/resolver", {**datos, "huella": "0" * 64}).status_code, 409)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_sesion_revocada_y_modo_no_persistente(self):
        self.iniciar()
        self.sesiones.cerrar(self.tokens[2])
        self.assertEqual(self.get(self.clientes[2]).status_code, 401)
        self.app.config["DEMO_PERSISTENTE"] = False
        self.assertEqual(self.get(self.clientes[0]).status_code, 404)

    def test_diagnostico_incompatible_no_filtra_contenido(self):
        self.iniciar()
        with conectar(self.base) as conexion:
            conexion.execute("PRAGMA ignore_check_constraints=ON")
            conexion.execute("UPDATE intenciones_operacion SET accion='dato-privado-invalido'")
        respuesta = self.get(self.clientes[2])
        self.assertEqual(respuesta.status_code, 409)
        self.assertNotIn("dato-privado-invalido", respuesta.get_data(as_text=True))
