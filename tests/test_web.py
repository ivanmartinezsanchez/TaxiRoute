"""Pruebas del prototipo local sin tocar el historial de uso."""
import re
import unittest
from taxiroute.carrera import Carrera
from taxiroute.web import crear_app


class PruebasWeb(unittest.TestCase):
    def setUp(self):
        self.tiempo = 0
        self.app = crear_app(lambda: Carrera(reloj=lambda: self.tiempo))
        self.app.config["TESTING"] = True
        self.cliente = self.app.test_client()
        html = self.cliente.get("/").get_data(as_text=True)
        self.token = re.search(r'name="taxiroute-token" content="([^"]+)"', html).group(1)

    def accion(self, nombre, revision):
        return self.cliente.post("/prototipo/accion",
            json={"accion": nombre, "revision": revision},
            headers={"X-TaxiRoute-Token": self.token})

    def test_ciclo_reutiliza_calculo_y_conserva_total(self):
        self.assertFalse(self.cliente.get("/prototipo/estado").json["activa"])
        self.assertEqual(self.accion("iniciar", 0).status_code, 200)
        self.tiempo = 10_000_000_000
        self.assertEqual(self.accion("mover", 1).json["importe"], "0.20")
        self.tiempo = 30_000_000_000
        final = self.accion("finalizar", 2).json
        self.assertEqual(final["importe"], "1.20")
        self.assertFalse(final["activa"])
        self.assertEqual(self.cliente.get("/prototipo/estado").json["importe"], "1.20")

    def test_accion_antigua_no_finaliza_otra_carrera(self):
        self.accion("iniciar", 0)
        self.accion("finalizar", 1)
        self.accion("iniciar", 2)
        self.assertEqual(self.accion("finalizar", 1).status_code, 409)
        self.assertTrue(self.cliente.get("/prototipo/estado").json["activa"])

    def test_sin_token_y_origen_externo_rechazados(self):
        self.assertEqual(self.cliente.post("/prototipo/accion",
            json={"accion": "iniciar", "revision": 0}).status_code, 403)
        respuesta = self.cliente.post("/prototipo/accion",
            json={"accion": "iniciar", "revision": 0},
            headers={"X-TaxiRoute-Token": self.token, "Origin": "https://otro.example"})
        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(self.cliente.get("/prototipo/estado").json["activa"])
        self.assertEqual(self.cliente.get("/", headers={"Host": "otro.example"}).status_code, 400)

    def test_acciones_invalidas_y_ciclo_protegido(self):
        self.assertEqual(self.accion("finalizar", 0).status_code, 409)
        self.assertEqual(self.accion("borrar", 0).status_code, 400)
        self.accion("iniciar", 0)
        self.assertEqual(self.accion("iniciar", 1).status_code, 409)

    def test_recargar_comparte_carrera_pero_otra_instancia_es_independiente(self):
        self.accion("iniciar", 0)
        self.assertTrue(self.app.test_client().get("/prototipo/estado").json["activa"])
        self.assertFalse(crear_app().test_client().get("/prototipo/estado").json["activa"])

    def test_recursos_locales_y_sin_cache(self):
        respuesta = self.cliente.get("/")
        self.assertIn("no-store", respuesta.headers["Cache-Control"])
        for recurso in ("/static/prototipo.css", "/static/prototipo.js"):
            with self.cliente.get(recurso) as respuesta:
                self.assertEqual(respuesta.status_code, 200)
