"""Integración parcial de sesiones con la consulta real de la web, sin red."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web import crear_app


class PruebasIntegracionParcial(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.clave = "ConductorPrueba123!"
        cuenta = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        self.clave_admin = "AdministradorPrueba123!"
        admin = crear_cuenta(ruta, nombre_usuario="Admin", nombre_visible="Admin",
                             contrasena=self.clave_admin, rol="administrador")
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id IN (?, ?)",
                             (cuenta.id, admin.id))
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                             (admin.id, "prueba@example.invalid"))
        self.ahora = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: self.ahora)
        self.buzon = []
        self.pin = ServicioPIN(ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append((correo, codigo)),
            reloj=lambda: self.ahora)
        self.carreras = ServicioCarreras()
        self.app = crear_app(self.carreras, modo_prueba=True, sesiones_prueba=self.sesiones,
                             pin_prueba=self.pin)
        self.app.testing = True
        self.cliente = self.app.test_client()
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def entrar(self):
        return self.cliente.post("/acceso/conductor", base_url=self.url,
            json={"usuario": "Ana", "contrasena": self.clave}, headers=self.origen)

    def estado(self):
        return self.cliente.get("/prototipo/estado", base_url=self.url)

    def test_acceso_y_cierre_protegen_estado_y_conservan_carrera(self):
        self.carreras.ejecutar("iniciar")
        self.assertEqual(self.estado().status_code, 401)
        self.assertEqual(self.entrar().status_code, 200)
        self.assertTrue(self.estado().json["activa"])
        salida = self.cliente.post("/acceso/cerrar", base_url=self.url, headers=self.origen)
        self.assertEqual(salida.status_code, 200)
        self.assertEqual(self.estado().status_code, 401)
        self.assertIsNotNone(self.carreras.carrera)

    def test_caducidad_y_credenciales_invalidas(self):
        fallo = self.cliente.post("/acceso/conductor", base_url=self.url,
            json={"usuario": "Ana", "contrasena": "incorrecta"}, headers=self.origen)
        self.assertEqual(fallo.status_code, 401)
        self.assertEqual(self.estado().status_code, 401)
        self.assertEqual(self.entrar().status_code, 401)  # Espera tras el fallo.
        self.ahora += timedelta(seconds=1)
        self.assertEqual(self.entrar().status_code, 200)
        self.ahora += timedelta(hours=1)
        self.assertEqual(self.estado().status_code, 401)

    def test_otros_endpoints_bloqueados_y_origen_invalido(self):
        self.assertEqual(self.cliente.get("/", base_url=self.url).status_code, 404)
        self.assertEqual(self.cliente.get("/prototipo/historial", base_url=self.url).status_code, 404)
        self.assertEqual(self.cliente.post("/prototipo/accion", base_url=self.url).status_code, 404)
        rechazo = self.cliente.post("/acceso/conductor", base_url=self.url,
            json={"usuario": "Ana", "contrasena": self.clave},
            headers={"Origin": "https://otro.example"})
        self.assertEqual(rechazo.status_code, 403)
        self.assertEqual(self.cliente.get("/prototipo/estado", base_url="http://localhost").status_code, 400)


    def test_administrador_requiere_contrasena_y_pin_para_consultar(self):
        ruta = "/acceso/administrador"
        inicio = self.cliente.post(ruta, base_url=self.url, headers=self.origen,
            json={"usuario": "Admin", "contrasena": self.clave_admin})
        self.assertEqual(inicio.json["paso"], "pin_pendiente")
        self.assertEqual(self.estado().status_code, 401)
        correo, codigo = self.buzon[-1]
        self.assertEqual(correo, "prueba@example.invalid")
        self.assertNotIn(codigo, inicio.get_data(as_text=True))
        verificacion = self.cliente.post(ruta + "/pin", base_url=self.url,
            headers=self.origen, json={"codigo": codigo})
        self.assertEqual(verificacion.json["paso"], "acceso_completado")
        self.assertEqual(self.estado().status_code, 200)
        self.assertEqual(self.cliente.get("/prototipo/historial", base_url=self.url).status_code, 404)
        self.assertEqual(self.cliente.post("/acceso/cerrar", base_url=self.url,
                         headers=self.origen).status_code, 200)
        self.assertEqual(self.estado().status_code, 401)
        self.assertEqual(self.cliente.post(ruta + "/pin", base_url=self.url,
                         headers=self.origen, json={"codigo": codigo}).status_code, 401)

    def test_pin_incorrecto_y_origen_externo_no_abren_sesion(self):
        ruta = "/acceso/administrador"
        inicio = self.cliente.post(ruta, base_url=self.url, headers=self.origen,
            json={"usuario": "Admin", "contrasena": self.clave_admin})
        self.assertEqual(inicio.status_code, 200)
        codigo = self.buzon[-1][1]
        ajeno = self.cliente.post(ruta + "/pin", base_url=self.url,
            headers={"Origin": "https://otro.example"}, json={"codigo": codigo})
        self.assertEqual(ajeno.status_code, 403)
        codigo_erroneo = "0000000" if codigo != "0000000" else "1111111"
        fallo = self.cliente.post(ruta + "/pin", base_url=self.url,
            headers=self.origen, json={"codigo": codigo_erroneo})
        self.assertEqual(fallo.status_code, 401)
        self.assertEqual(self.estado().status_code, 401)
    def test_no_permite_activar_fuera_de_pruebas(self):
        with self.assertRaises(ValueError):
            crear_app(self.carreras, sesiones_prueba=self.sesiones)
        with self.assertRaises(ValueError):
            crear_app(modo_prueba=True, sesiones_prueba=self.sesiones)
        with self.assertRaises(ValueError):
            crear_app(self.carreras, modo_prueba=True, pin_prueba=self.pin)