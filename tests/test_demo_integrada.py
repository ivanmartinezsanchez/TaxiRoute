"""Demostración integrada con usuarios, buzón y archivos desechables."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO, crear_demo_temporal


class PruebasDemoIntegrada(unittest.TestCase):
    def test_datos_ficticios_y_recorridos_por_cliente_flask(self):
        with TemporaryDirectory() as temporal:
            buzon = []
            app = crear_demo_temporal(temporal, mostrar_codigo=buzon.append)
            app.testing = True
            conductor, administrador = app.test_client(), app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            def post(cliente, ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            self.assertEqual(post(conductor, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
            pantalla = conductor.get("/taximetro", base_url=url)
            self.assertEqual(pantalla.status_code, 200)
            self.assertIn('id="mantener-pantalla"', pantalla.get_data(as_text=True))
            self.assertIn('pantalla_activa.js', pantalla.get_data(as_text=True))
            recurso = conductor.get("/static/pantalla_activa.js", base_url=url)
            self.assertEqual(recurso.status_code, 200)
            recurso.close()
            self.assertEqual(post(conductor, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(post(conductor, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
            self.assertEqual(len(conductor.get("/api/historial", base_url=url).json["carreras"]), 1)
            inicio = post(administrador, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO})
            self.assertEqual(inicio.json["paso"], "pin_pendiente")
            self.assertEqual(len(buzon), 1)
            self.assertNotIn(buzon[0], inicio.get_data(as_text=True))
            self.assertEqual(post(administrador, "/acceso/administrador/pin", {"codigo": buzon[0]}).json["paso"], "acceso_completado")
            self.assertEqual(administrador.get("/api/historial", base_url=url).json["carreras"], [])
            self.assertTrue((Path(temporal) / "historial.sqlite3").is_file())
        self.assertFalse(Path(temporal).exists())
    def test_tarifas_de_la_carrera_activa_y_la_siguiente(self):
        with TemporaryDirectory() as temporal:
            buzon = []
            app = crear_demo_temporal(temporal, mostrar_codigo=buzon.append)
            app.testing = True
            conductor, admin = app.test_client(), app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            def post(cliente, ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            self.assertEqual(post(conductor, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
            self.assertEqual(post(conductor, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(post(conductor, "/api/tarifas", {"parado": "0.03", "en_movimiento": "0.06"}).status_code, 403)
            self.assertEqual(post(admin, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO}).status_code, 200)
            self.assertEqual(post(admin, "/acceso/administrador/pin", {"codigo": buzon[-1]}).status_code, 200)
            self.assertEqual(post(admin, "/api/tarifas", {"parado": "0.03", "en_movimiento": "0.06"}).status_code, 200)
            actuales = conductor.get("/api/carrera/tarifas", base_url=url).json["tarifas"]
            nuevas = conductor.get("/api/tarifas", base_url=url).json["tarifas"]
            self.assertEqual(actuales, {"parado": "0.02", "en movimiento": "0.05"})
            self.assertEqual(nuevas, {"parado": "0.03", "en movimiento": "0.06"})
            self.assertEqual(post(conductor, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
            self.assertEqual(post(conductor, "/api/carrera/iniciar", {"revision": 2}).status_code, 200)
            self.assertEqual(post(conductor, "/api/carrera/finalizar", {"revision": 3}).status_code, 200)
            filas = conductor.get("/api/historial", base_url=url).json["carreras"]
            self.assertEqual([fila["tarifa_parado"] for fila in filas], ["0.02", "0.03"])
    def test_admin_crea_conductor_que_cambia_contrasena_y_accede(self):
        with TemporaryDirectory() as temporal:
            buzon = []
            app = crear_demo_temporal(temporal, mostrar_codigo=buzon.append)
            app.testing = True
            admin, nuevo = app.test_client(), app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            def post(cliente, ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            post(admin, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO})
            self.assertEqual(post(admin, "/acceso/administrador/pin", {"codigo": buzon[-1]}).status_code, 200)
            temporal_clave = "TemporalPrueba123!"
            creada = post(admin, "/api/administracion/conductores", {"usuario": "NuevoDemo",
                "nombre_visible": "Conductor de prueba", "contrasena": temporal_clave})
            self.assertEqual(creada.status_code, 201)
            self.assertNotIn(temporal_clave, creada.get_data(as_text=True))
            self.assertTrue(creada.json["cambio_obligatorio"])
            self.assertEqual(post(nuevo, "/acceso/conductor", {"usuario": "NuevoDemo", "contrasena": temporal_clave}).status_code, 401)
            nueva_clave = "NuevaPrueba456!"
            cambio = post(nuevo, "/acceso/conductor/cambio", {"usuario": "NuevoDemo",
                "contrasena_actual": temporal_clave, "contrasena_nueva": nueva_clave})
            self.assertEqual(cambio.json["paso"], "volver_a_iniciar")
            self.assertEqual(post(nuevo, "/acceso/conductor", {"usuario": "NuevoDemo", "contrasena": nueva_clave}).status_code, 200)
            self.assertEqual(nuevo.get("/taximetro", base_url=url).status_code, 200)
    def test_admin_restablece_clave_y_revoca_la_sesion_del_conductor(self):
        with TemporaryDirectory() as temporal:
            buzon = []
            app = crear_demo_temporal(temporal, mostrar_codigo=buzon.append)
            app.testing = True
            conductor, admin, nuevo = app.test_client(), app.test_client(), app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            def post(cliente, ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            self.assertEqual(post(conductor, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
            post(admin, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO})
            post(admin, "/acceso/administrador/pin", {"codigo": buzon[-1]})
            cuentas = admin.get("/api/administracion/conductores", base_url=url).json["conductores"]
            cuenta_id = next(cuenta["id"] for cuenta in cuentas if cuenta["usuario"] == "AnaDemo")
            ruta = "/api/administracion/conductores/" + cuenta_id + "/restablecer-contrasena"
            clave = "TemporalSegura123!"
            self.assertEqual(post(conductor, ruta, {"contrasena": clave}).status_code, 403)
            self.assertEqual(post(admin, ruta, {"contrasena": "breve"}).status_code, 400)
            respuesta = post(admin, ruta, {"contrasena": clave})
            self.assertEqual(respuesta.status_code, 200)
            self.assertNotIn(clave, respuesta.get_data(as_text=True))
            self.assertEqual(conductor.get("/taximetro", base_url=url).status_code, 401)
            self.assertEqual(post(nuevo, "/acceso/conductor/cambio", {"usuario": "AnaDemo", "contrasena_actual": clave,
                "contrasena_nueva": "NuevaSegura456!"}).json["paso"], "volver_a_iniciar")
            self.assertEqual(post(nuevo, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": "NuevaSegura456!"}).status_code, 200)

    def test_modo_durable_experimental_dos_carreras(self):
        from uuid import uuid4
        with TemporaryDirectory() as temporal:
            app = crear_demo_temporal(temporal, mostrar_codigo=lambda _: None, durable=True)
            app.testing = True
            cliente = app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            def post(ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            self.assertEqual(post("/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
            html = cliente.get("/taximetro", base_url=url).get_data(as_text=True)
            self.assertIn('data-durable="true"', html)
            inicio = post("/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual((inicio.status_code, inicio.json["revision"]), (200, 1))
            self.assertIsNotNone(cliente.get("/api/carrera/tarifas", base_url=url).json["tarifas"])
            cierre = post("/api/carrera/finalizar", {"revision": 1})
            self.assertEqual((cierre.status_code, cierre.json["revision"]), (200, 2))
            self.assertEqual(len(cliente.get("/api/historial", base_url=url).json["carreras"]), 1)
            segunda = post("/api/carrera/iniciar", {"revision": 2, "id_operacion": str(uuid4())})
            self.assertEqual((segunda.status_code, segunda.json["revision"]), (200, 3))
