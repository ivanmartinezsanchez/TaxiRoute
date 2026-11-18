"""Recuperación web del servidor SQLite bajo el bloqueo de la instalación."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.servidor_sqlite_local import servidor_sqlite_experimental


class PruebasRecuperacionServidorSQLite(unittest.TestCase):
    def test_fabrica_sin_bloqueo_no_habilita_recuperacion_por_marca_demo(self):
        from taxiroute.composicion_experimental import crear_app_configurada
        with TemporaryDirectory() as temporal:
            datos = preparar_demo_sqlite(Path(temporal) / "datos")
            app = crear_app_configurada(ruta_cuentas=datos / "cuentas.sqlite3",
                ruta_historial=datos / "historial.sqlite3", ruta_tarifas=datos / "tarifas.json",
                clave_hmac=b"x" * 32, enviar_pin=lambda *_: None, durable=True)
            cliente = app.test_client()
            self.assertEqual(cliente.get("/api/carrera/revision-recuperacion", base_url="https://localhost").status_code, 404)
            app.config["DEMO_PERSISTENTE"] = True
            self.assertEqual(cliente.get("/api/carrera/revision-recuperacion", base_url="https://localhost").status_code, 404)

    def test_carrera_interrumpida_puede_revisarse_sin_modo_demo(self):
        with TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            datos = preparar_demo_sqlite(raiz / "datos")
            certificado, clave = certificado_temporal(raiz)
            opciones = dict(ruta_cuentas=datos / "cuentas.sqlite3", ruta_historial=datos / "historial.sqlite3",
                ruta_tarifas=datos / "tarifas.json", certificado=certificado, clave=clave,
                puerto=0, enviar_pin=lambda *_: None)
            url, cabeceras = "https://localhost", {"Origin": "https://localhost"}
            def cliente(app):
                c = app.test_client()
                acceso = c.post("/acceso/conductor", base_url=url, headers=cabeceras,
                    json={"usuario": "AnaDemo", "contrasena": "ConductorDemo123!"})
                self.assertEqual(acceso.status_code, 200)
                return c
            with servidor_sqlite_experimental(**opciones) as servidor:
                app = servidor.app
                self.assertFalse(app.config.get("DEMO_PERSISTENTE", False))
                self.assertTrue(app.config.get("RECUPERACION_HABILITADA"))
                c = cliente(app)
                pantalla = c.get("/taximetro", base_url=url).get_data(as_text=True)
                self.assertIn('data-persistente="true"', pantalla)
                self.assertIn('Instalación experimental: utiliza solo datos de prueba.', pantalla)
                inicio = c.post("/api/carrera/iniciar", base_url=url, headers=cabeceras,
                    json={"revision": 0, "id_operacion": str(uuid4())})
                self.assertEqual(inicio.status_code, 200)
                self.assertEqual(app.extensions["control_periodico"].ciclo(), 0)
            with servidor_sqlite_experimental(**opciones) as servidor:
                c = cliente(servidor.app)
                vista = c.get("/api/carrera/revision-recuperacion", base_url=url)
                self.assertEqual(vista.status_code, 200)
                punto = vista.json["pendiente"]
                datos_confirmacion = {"revision": punto["revision_recuperacion"],
                    "ultimo_control": punto["ultimo_control"], "huella": punto["huella"], "confirmar": True}
                cierre = c.post("/api/carrera/confirmar-recuperacion", base_url=url,
                                headers=cabeceras, json=datos_confirmacion)
                self.assertEqual(cierre.status_code, 200)
                historial = c.get("/api/historial", base_url=url).json["carreras"]
                self.assertEqual(len(historial), 1)
                self.assertEqual(historial[0]["fin"], punto["ultimo_control"])
                self.assertEqual(historial[0]["importe"], punto["importe_recuperado"])


if __name__ == "__main__":
    unittest.main()
