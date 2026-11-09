"""Composición sobre archivos temporales, sin servidor ni correo real."""
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.base_cuentas import inicializar_base
from taxiroute.composicion_experimental import crear_app_configurada
from taxiroute.configuracion import guardar_tarifas
from taxiroute.demo_integrada import CLAVE_CONDUCTOR_DEMO, crear_demo_temporal
from taxiroute.historial_sqlite import HistorialSQLite


class PruebasComposicionExperimental(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        cuentas, historial, tarifas = (carpeta / nombre for nombre in ("cuentas", "historial", "tarifas"))
        inicializar_base(cuentas)
        HistorialSQLite(historial).inicializar()
        guardar_tarifas(tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
        self.argumentos = dict(ruta_cuentas=cuentas, ruta_historial=historial, ruta_tarifas=tarifas,
                               clave_hmac=token_bytes(32), enviar_pin=lambda correo, codigo: None, durable=True)

    def test_monta_sin_iniciar_servidor_ni_control_periodico(self):
        app = crear_app_configurada(**self.argumentos)
        self.assertIsNone(app.extensions["control_periodico"]._hilo)
        self.assertEqual(app.test_client().get("/acceso", base_url="https://localhost").status_code, 200)

    def test_archivo_ausente_no_se_crea(self):
        ruta = Path(self.argumentos["ruta_historial"])
        ruta.unlink()
        with self.assertRaises(ValueError):
            crear_app_configurada(**self.argumentos)
        self.assertFalse(ruta.exists())

    def test_rechaza_archivos_repetidos_y_configuracion_invalida(self):
        for cambios in ({"ruta_historial": self.argumentos["ruta_cuentas"]},
                        {"clave_hmac": b"corta"}, {"enviar_pin": None}, {"durable": "si"}):
            with self.subTest(cambios=tuple(cambios)), self.assertRaises((TypeError, ValueError)):
                crear_app_configurada(**(self.argumentos | cambios))

    def test_no_inicia_modo_sin_recuperacion_con_carrera_pendiente(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            app = crear_demo_temporal(carpeta, mostrar_codigo=lambda _: None, durable=True)
            app.testing = True
            cliente = app.test_client()
            origen = {"Origin": "https://localhost"}
            acceso = cliente.post("/acceso/conductor", base_url="https://localhost", headers=origen,
                json={"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO})
            self.assertEqual(acceso.status_code, 200)
            inicio = cliente.post("/api/carrera/iniciar", base_url="https://localhost", headers=origen,
                json={"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
            with self.assertRaisesRegex(ValueError, "pendientes"):
                crear_app_configurada(ruta_cuentas=carpeta / "cuentas.sqlite3",
                    ruta_historial=carpeta / "historial.sqlite3", ruta_tarifas=carpeta / "tarifas.json",
                    clave_hmac=token_bytes(32), enviar_pin=lambda correo, codigo: None, durable=False)


if __name__ == "__main__":
    unittest.main()
