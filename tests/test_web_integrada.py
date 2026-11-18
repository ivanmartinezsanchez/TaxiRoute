"""Integración aislada con HTTPS simulado, cuentas y buzón temporales."""
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from jinja2 import ChoiceLoader, DictLoader
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


class PruebasWebIntegrada(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "ConductorPrueba123!"
        self.clave_admin = "AdministradorPrueba123!"
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana <Prueba>", contrasena=self.clave)
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin", contrasena=self.clave_admin, rol="administrador")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (admin.id, "demo@example.invalid"))
        fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: fecha)
        self.buzon = []
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append((correo, codigo)), reloj=lambda: fecha)
        def historial(usuario):
            return HistorialCSV(carpeta / usuario / "historial.csv")
        self.flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(historial=historial(usuario)))
        self.app = crear_app_integrada(self.sesiones, self.pin, self.flota, historial)
        self.app.testing = True
        self.app.jinja_loader = ChoiceLoader([
            DictLoader({"taximetro_seguro.html": "<p>Taxímetro {{ nombre }}</p>"}), self.app.jinja_loader])
        self.cliente = self.app.test_client()
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def login_conductor(self):
        return self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": self.clave})

    def test_conductor_inicia_y_accede_a_pantalla_y_api(self):
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 401)
        self.assertEqual(self.cliente.get("/api/carrera", base_url=self.url).status_code, 401)
        respuesta = self.login_conductor()
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Secure", respuesta.headers["Set-Cookie"])
        pantalla = self.cliente.get("/taximetro", base_url=self.url)
        self.assertEqual(pantalla.status_code, 200)
        self.assertIn("Ana &lt;Prueba&gt;", pantalla.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/api/carrera", base_url=self.url).json["revision"], 0)
        self.assertTrue(self.post("/api/carrera/iniciar", {"revision": 0}).json["activa"])
        self.assertEqual(self.cliente.post("/acceso/cerrar", base_url=self.url, headers=self.origen).status_code, 200)
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 401)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)

    def test_admin_requiere_pin_y_no_muestra_codigo_en_respuesta(self):
        inicio = self.post("/acceso/administrador", {"usuario": "Admin", "contrasena": self.clave_admin})
        self.assertEqual(inicio.json["paso"], "pin_pendiente")
        correo, codigo = self.buzon[-1]
        self.assertEqual(correo, "demo@example.invalid")
        self.assertNotIn(codigo, inicio.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 401)
        verificacion = self.post("/acceso/administrador/pin", {"codigo": codigo})
        self.assertEqual(verificacion.json["paso"], "acceso_completado")
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 200)
        self.assertEqual(self.post("/api/carrera/iniciar", {"revision": 0}).status_code, 200)

    def test_http_host_y_cabeceras_reenviadas_no_se_aceptan(self):
        for ruta in ("/", "/acceso", "/taximetro", "/static/acceso.css", "/api/carrera"):
            self.assertEqual(self.cliente.get(ruta, base_url="http://localhost").status_code, 400)
        r = self.cliente.get("/acceso", base_url="http://localhost", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cliente.get("/acceso", base_url="https://otro.example").status_code, 400)

    def test_recursos_acotados_cabeceras_y_tamano(self):
        acceso = self.cliente.get("/acceso", base_url=self.url)
        self.assertEqual(acceso.status_code, 200)
        self.assertEqual(acceso.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", acceso.headers["Content-Security-Policy"])
        recurso = self.cliente.get("/static/acceso.js", base_url=self.url)
        self.assertEqual(recurso.status_code, 200)
        recurso.close()
        self.assertEqual(self.cliente.get("/static/demo_acceso.js", base_url=self.url).status_code, 404)
        self.assertEqual(self.cliente.get("/prototipo/estado", base_url=self.url).status_code, 404)
        respuesta = self.post("/acceso/conductor", {"usuario": "a" * 5000, "contrasena": "x"})
        self.assertEqual(respuesta.status_code, 413)

    def test_pin_y_sesion_de_bases_distintas_se_rechazan(self):
        diferente = ServicioSesiones(self.ruta.parent / "otra.sqlite3")
        with self.assertRaises(ValueError):
            crear_app_integrada(diferente, self.pin, self.flota, lambda _: None)