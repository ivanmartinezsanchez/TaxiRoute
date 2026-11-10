"""Límite web local: no expone credenciales ni utiliza red real."""
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.limitador_acceso import LimitadorAcceso
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


class PruebasLimiteWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.clave = "ConductorPrueba123!"
        cuenta = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Prueba", contrasena=self.clave)
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
        fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: fecha)
        pin = ServicioPIN(ruta, clave_hmac=token_bytes(32), enviar=lambda *_: None, reloj=lambda: fecha)
        def historial(usuario):
            return HistorialCSV(carpeta / usuario / "historial.csv")
        flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(historial=historial(usuario)))
        self.reloj = [100.0]
        self.limite = LimitadorAcceso(maximo=3, periodo=60, reloj=lambda: self.reloj[0])
        app = crear_app_integrada(self.sesiones, pin, flota, historial, limitador_acceso=self.limite)
        app.testing = True
        self.cliente = app.test_client()
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def test_limite_compartido_no_corta_sesion_y_se_recupera(self):
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": self.clave}).status_code, 200)
        self.assertEqual(self.post("/acceso/administrador", {}).status_code, 400)
        self.assertEqual(self.post("/acceso/administrador/pin", {}).status_code, 400)
        with patch.object(self.sesiones, "iniciar_conductor") as comprobar:
            respuesta = self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": self.clave})
        comprobar.assert_not_called()
        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.headers["Retry-After"], "60")
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertIn("Espera 60 segundos", respuesta.json["error"])
        self.assertEqual(self.cliente.get("/taximetro", base_url=self.url).status_code, 200)
        self.assertEqual(self.post("/api/carrera/iniciar", {"revision": 0}).status_code, 200)
        self.assertEqual(self.post("/api/carrera/finalizar", {"revision": 1}).status_code, 200)
        self.assertEqual(self.cliente.post("/acceso/cerrar", base_url=self.url, headers=self.origen).status_code, 200)
        self.reloj[0] += 60
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": self.clave}).status_code, 200)

    def test_origen_externo_no_consume_cuota_y_se_rechaza(self):
        for _ in range(5):
            respuesta = self.cliente.post("/acceso/conductor", base_url=self.url,
                headers={"Origin": "https://ajeno.invalid"}, json={})
            self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.post("/acceso/conductor", {"usuario": "Ana", "contrasena": self.clave}).status_code, 200)

    def test_todas_las_rutas_de_credenciales_comparten_el_mismo_limite(self):
        rutas = ("/acceso/conductor", "/acceso/conductor/cambio", "/acceso/administrador",
                 "/acceso/administrador/pin", "/acceso/administrador/cambio")
        self.assertEqual([self.post(ruta, {}).status_code for ruta in rutas], [400, 400, 400, 429, 429])


if __name__ == "__main__":
    unittest.main()
