"""Recorrido de la interfaz aislada sin servidor de red ni correo real."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_admin_login import COOKIE_PIN, RUTA_ADMIN
from taxiroute.web_demo_acceso import crear_app_demo_acceso


class PruebasDemoAcceso(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdministradorPrueba123!"
        cuenta = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="<Admin>",
                              contrasena=self.clave, rol="administrador")
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
            c.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                      (cuenta.id, "prueba@example.invalid"))
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.buzon = []
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append(codigo), reloj=lambda: self.fecha)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.app = crear_app_demo_acceso(self.pin, self.sesiones)
        self.app.config["TESTING"] = True
        self.cliente = self.app.test_client()
        self.origen = {"Origin": "https://localhost"}

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url="https://localhost",
                                 headers=self.origen, json=datos)

    def iniciar(self):
        return self.post(RUTA_ADMIN, {"usuario": "Admin", "contrasena": self.clave})

    def panel(self):
        return self.cliente.get("/panel", base_url="https://localhost")

    def test_interfaz_y_panel_protegido(self):
        r = self.cliente.get("/", base_url="https://localhost")
        self.assertEqual(r.status_code, 200)
        self.assertIn("form-credenciales", r.get_data(as_text=True))
        self.assertIn("no-store", r.headers["Cache-Control"])
        self.assertEqual(self.panel().status_code, 401)
        self.assertEqual(self.iniciar().json["paso"], "pin_pendiente")
        self.assertEqual(self.panel().status_code, 401)
        r = self.post(RUTA_ADMIN + "/pin", {"codigo": self.buzon[-1]})
        self.assertEqual(r.json["paso"], "acceso_completado")
        panel = self.panel()
        self.assertEqual(panel.status_code, 200)
        self.assertIn("&lt;Admin&gt;", panel.get_data(as_text=True))
        self.assertNotIn("<Admin>", panel.get_data(as_text=True))

    def test_pin_reutilizado_y_expiracion_de_sesion(self):
        self.iniciar()
        codigo = self.buzon[-1]
        cookie = self.cliente.get_cookie(COOKIE_PIN, path=RUTA_ADMIN).value
        self.assertEqual(self.post(RUTA_ADMIN + "/pin", {"codigo": codigo}).status_code, 200)
        self.cliente.set_cookie(COOKIE_PIN, cookie, path=RUTA_ADMIN, secure=True)
        self.assertEqual(self.post(RUTA_ADMIN + "/pin", {"codigo": codigo}).status_code, 401)
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.panel().status_code, 401)

    def test_password_incorrecta_no_envia_ni_abre_panel(self):
        r = self.post(RUTA_ADMIN, {"usuario": "Admin", "contrasena": "incorrecta"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.buzon, [])
        self.assertEqual(self.panel().status_code, 401)

    def test_cambio_obligatorio_no_abre_panel_hasta_nuevo_acceso(self):
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=1")
        self.iniciar()
        self.assertEqual(self.post(RUTA_ADMIN + "/pin", {"codigo": self.buzon[-1]}).json["paso"],
                         "cambio_pendiente")
        self.assertEqual(self.panel().status_code, 401)
        nueva = "NuevaTemporal456!"
        self.assertEqual(self.post(RUTA_ADMIN + "/cambio", {"contrasena_nueva": nueva}).json["paso"],
                         "volver_a_iniciar")
        self.assertEqual(self.panel().status_code, 401)
        self.fecha += timedelta(minutes=1)
        self.clave = nueva
        self.iniciar()
        self.assertEqual(self.post(RUTA_ADMIN + "/pin", {"codigo": self.buzon[-1]}).status_code, 200)
        self.assertEqual(self.panel().status_code, 200)

    def test_recursos_locales_disponibles_y_host_rechazado(self):
        for ruta in ("/static/demo_acceso.css", "/static/demo_acceso.js"):
            respuesta = self.cliente.get(ruta, base_url="https://localhost")
            self.assertEqual(respuesta.status_code, 200)
            respuesta.close()
        self.assertEqual(self.cliente.get("/", base_url="https://otro.example").status_code, 400)
