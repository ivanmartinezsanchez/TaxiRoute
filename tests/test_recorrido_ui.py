"""Recorrido integrado con plantillas reales y datos ficticios; HTTPS simulado."""
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


class PruebasRecorridoUI(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.ana = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        admin = crear_cuenta(ruta, nombre_usuario="Admin", nombre_visible="Admin", contrasena="AdministradorPrueba123!", rol="administrador")
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (admin.id, "demo@example.invalid"))
        fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: fecha)
        self.buzon = []
        self.pin = ServicioPIN(ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append((correo, codigo)), reloj=lambda: fecha)
        historial_ruta = carpeta / "historial.sqlite3"
        historial = lambda usuario: HistorialSQLite(historial_ruta, usuario_id=usuario)
        self.tiempo = 0
        self.flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo), historial=historial(usuario)))
        self.app = crear_app_integrada(self.sesiones, self.pin, self.flota, historial)
        self.app.testing = True
        self.cliente = self.app.test_client()
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def test_pantallas_reales_y_flujo_conductor(self):
        acceso = self.cliente.get("/acceso", base_url=self.url)
        self.assertEqual(acceso.status_code, 200)
        self.assertIn('id="form-acceso"', acceso.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 401)
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": "ConductorPrueba123!"}).status_code, 200)
        pantalla = self.cliente.get("/taximetro", base_url=self.url)
        self.assertEqual(pantalla.status_code, 200)
        self.assertIn('id="iniciar"', pantalla.get_data(as_text=True))
        self.assertIn('Modo de pruebas', pantalla.get_data(as_text=True))
        for recurso in ("taximetro_seguro.css", "taximetro_seguro.js"):
            respuesta = self.cliente.get("/static/" + recurso, base_url=self.url)
            self.assertEqual(respuesta.status_code, 200)
            respuesta.close()
        self.assertEqual(self.post("/api/carrera/iniciar", {"revision": 0}).status_code, 200)
        self.tiempo = 10_000_000_000
        self.assertEqual(self.post("/api/carrera/finalizar", {"revision": 1}).json["importe"], "0.20")
        self.assertEqual(len(self.cliente.get("/api/historial", base_url=self.url).json["carreras"]), 1)

    def test_admin_gestiona_conductor_y_revoca_sesion(self):
        conductor = self.app.test_client()
        acceso = conductor.post("/acceso/conductor", base_url=self.url, headers=self.origen,
            json={"usuario": "Ana", "contrasena": "ConductorPrueba123!"})
        self.assertEqual(acceso.status_code, 200)
        self.assertEqual(conductor.get("/administracion", base_url=self.url).status_code, 403)
        self.post("/acceso/administrador", {"usuario": "Admin", "contrasena": "AdministradorPrueba123!"})
        self.post("/acceso/administrador/pin", {"codigo": self.buzon[-1][1]})
        panel = self.cliente.get("/administracion", base_url=self.url)
        self.assertEqual(panel.status_code, 200)
        self.assertIn("Cuentas de conductores", panel.get_data(as_text=True))
        listado = self.cliente.get("/api/administracion/conductores", base_url=self.url)
        self.assertEqual(len(listado.json["conductores"]), 1)
        self.assertNotIn("hash_contrasena", str(listado.json))
        accion = self.post("/api/administracion/conductores/" + self.ana.id + "/accion", {"accion": "desactivar"})
        self.assertEqual(accion.status_code, 200)
        self.assertEqual(conductor.get("/api/carrera", base_url=self.url).status_code, 401)
        self.assertFalse(self.cliente.get("/api/administracion/conductores", base_url=self.url).json["conductores"][0]["habilitado"])
    def test_conductor_temporal_cambia_clave_y_luego_accede(self):
        crear_cuenta(self.sesiones.ruta, nombre_usuario="Nuevo", nombre_visible="Nuevo",
                     contrasena="TemporalPrueba123!")
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Nuevo", "contrasena": "TemporalPrueba123!"}).status_code, 401)
        cambio = self.post("/acceso/conductor/cambio", {"usuario": "Nuevo",
            "contrasena_actual": "TemporalPrueba123!", "contrasena_nueva": "NuevaPrueba456!"})
        self.assertEqual(cambio.json["paso"], "volver_a_iniciar")
        self.assertNotIn("Set-Cookie", cambio.headers)
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Nuevo", "contrasena": "NuevaPrueba456!"}).status_code, 200)
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 200)
    def test_admin_solo_accede_a_pantalla_tras_pin(self):
        inicio = self.post("/acceso/administrador", {"usuario": "Admin", "contrasena": "AdministradorPrueba123!"})
        self.assertEqual(inicio.json["paso"], "pin_pendiente")
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 401)
        codigo = self.buzon[-1][1]
        self.assertNotIn(codigo, inicio.get_data(as_text=True))
        self.assertEqual(self.post("/acceso/administrador/pin", {"codigo": codigo}).json["paso"], "acceso_completado")
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 200)