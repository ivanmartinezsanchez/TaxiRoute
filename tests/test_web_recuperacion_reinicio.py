"""Recuperación web tras reinicio con una demo ficticia y carpeta temporal."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from taxiroute.demo_integrada import CLAVE_CONDUCTOR_DEMO, demo_persistente


class PruebasRecuperacionWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name) / "demo"
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def cliente(self, app):
        app.testing = True
        cliente = app.test_client()
        respuesta = cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                                 json={"usuario":"AnaDemo", "contrasena":CLAVE_CONDUCTOR_DEMO})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def post(self, cliente, ruta, datos, headers=None):
        return cliente.post(ruta, base_url=self.url, headers=self.origen if headers is None else headers, json=datos)

    def interrumpir(self):
        with demo_persistente(self.carpeta, mostrar_codigo=lambda _: None) as app:
            cliente = self.cliente(app)
            respuesta = self.post(cliente, "/api/carrera/iniciar", {"revision":0, "id_operacion":str(uuid4())})
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(app.extensions["control_periodico"].ciclo(), 0)
            pendiente = cliente.get("/api/recuperacion/pendiente", base_url=self.url).json["pendiente"]
            return pendiente

    def test_reiniciar_revisar_confirmar_y_empezar_otra_carrera(self):
        anterior = self.interrumpir()
        with demo_persistente(self.carpeta, mostrar_codigo=lambda _: None) as app:
            cliente = self.cliente(app)
            self.assertEqual(cliente.get("/api/carrera", base_url=self.url).status_code, 409)
            html = cliente.get("/taximetro", base_url=self.url).get_data(as_text=True)
            self.assertIn('data-persistente="true"', html)
            self.assertIn("datos de la demostración se conservan", html)
            vista = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url)
            self.assertEqual(vista.status_code, 200)
            punto = vista.json["pendiente"]
            self.assertEqual(punto["ultimo_control"], anterior["ultimo_control"])
            datos = {"revision":punto["revision_recuperacion"], "ultimo_control":punto["ultimo_control"], "huella":punto["huella"], "confirmar":True}
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", {**datos, "confirmar":False}).status_code, 400)
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", {**datos, "revision":datos["revision"]+1}).status_code, 409)
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos, headers={}).status_code, 403)
            cierre = self.post(cliente, "/api/carrera/confirmar-recuperacion", datos)
            self.assertEqual(cierre.status_code, 200)
            historial = cliente.get("/api/historial", base_url=self.url).json["carreras"]
            self.assertEqual(len(historial), 1)
            self.assertEqual(historial[0]["fin"], punto["ultimo_control"])
            self.assertEqual(historial[0]["importe"], punto["importe_recuperado"])
            otra = self.post(cliente, "/api/carrera/iniciar", {"revision":cierre.json["revision"], "id_operacion":str(uuid4())})
            self.assertEqual(otra.status_code, 200)

    def test_fallo_tras_guardar_permanece_disponible_para_reintento(self):
        self.interrumpir()
        with demo_persistente(self.carpeta, mostrar_codigo=lambda _: None) as app:
            cliente = self.cliente(app)
            punto = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
            datos = {"revision":punto["revision_recuperacion"], "ultimo_control":punto["ultimo_control"], "huella":punto["huella"], "confirmar":True}
            with patch("taxiroute.recuperacion_ultima_captura.liberar_recuperada", side_effect=OSError("simulado")):
                self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 503)
            self.assertEqual(cliente.get("/api/carrera", base_url=self.url).status_code, 409)
            nueva_vista = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url)
            self.assertEqual(nueva_vista.status_code, 200)
            self.assertEqual(nueva_vista.json["pendiente"]["estado"], "guardada")
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 200)
            self.assertEqual(len(cliente.get("/api/historial", base_url=self.url).json["carreras"]), 1)

    def test_no_confirma_si_cambia_el_importe_sin_cambiar_revision(self):
        from taxiroute.base_cuentas import conectar
        self.interrumpir()
        with demo_persistente(self.carpeta, mostrar_codigo=lambda _: None) as app:
            cliente = self.cliente(app)
            punto = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
            datos = {"revision":punto["revision_recuperacion"], "ultimo_control":punto["ultimo_control"], "huella":punto["huella"], "confirmar":True}
            with conectar(self.carpeta / "cuentas.sqlite3") as conexion:
                conexion.execute("UPDATE carreras_recuperacion SET parado_ns=parado_ns+1000000000 WHERE estado_registro='activa'")
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 409)
            self.assertEqual(cliente.get("/api/historial", base_url=self.url).json["carreras"], [])
