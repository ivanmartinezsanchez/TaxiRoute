"""Pruebas aisladas de API con usuarios y archivos temporales."""
import errno
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from taxiroute.api_flota import crear_api_flota
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.configuracion import ConfiguracionError
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_login import crear_acceso_conductor


class PruebasApiFlota(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.ana = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.ahora = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: self.ahora)
        def historial(usuario_id):
            return HistorialCSV(carpeta / usuario_id / "historial.csv")
        self.flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo), historial=historial(usuario)))
        app = Flask(__name__)
        app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
        app.register_blueprint(crear_acceso_conductor(self.sesiones))
        app.register_blueprint(crear_api_flota(self.sesiones, self.flota, historial))
        self.ana_web, self.luis_web = app.test_client(), app.test_client()
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}

    def login(self, cliente, usuario):
        return cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
            json={"usuario": usuario, "contrasena": "ConductorPrueba123!"})

    def post(self, cliente, ruta, datos, *, headers=None, url=None):
        return cliente.post(ruta, base_url=url or self.url,
                            headers=self.origen if headers is None else headers, json=datos)

    def test_flujo_dos_usuarios_y_historial_aislado(self):
        self.assertEqual(self.login(self.ana_web, "Ana").status_code, 200)
        self.assertEqual(self.login(self.luis_web, "Luis").status_code, 200)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}).json["revision"], 1)
        self.assertEqual(self.post(self.luis_web, "/api/carrera/iniciar", {"revision": 0}).json["revision"], 1)
        self.tiempo = 10_000_000_000
        self.assertEqual(self.post(self.ana_web, "/api/carrera/estado", {"revision": 1, "estado": "en movimiento"}).json["revision"], 2)
        self.tiempo = 20_000_000_000
        self.assertEqual(self.post(self.ana_web, "/api/carrera/finalizar", {"revision": 2}).json["importe"], "0.70")
        self.assertEqual(self.luis_web.get("/api/carrera", base_url=self.url).json["importe"], "0.40")
        ana = self.ana_web.get("/api/historial", base_url=self.url)
        luis = self.luis_web.get("/api/historial", base_url=self.url)
        self.assertEqual([fila["importe"] for fila in ana.json["carreras"]], ["0.70"])
        self.assertEqual(luis.json["carreras"], [])
        self.assertEqual(ana.headers["Cache-Control"], "no-store")

    def test_sin_sesion_y_origenes_invalidos_no_actuan(self):
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).status_code, 401)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}).status_code, 401)
        self.login(self.ana_web, "Ana")
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}, headers=headers).status_code, 403)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}, url="http://localhost").status_code, 400)
        self.assertIsNone(self.flota.consultar(self.ana.id))

    def test_no_admite_identidad_externa_ni_orden_desactualizada(self):
        self.login(self.ana_web, "Ana")
        ruta = "/api/carrera/iniciar"
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": 0, "usuario_id": self.luis.id}).status_code, 400)
        self.assertEqual(self.post(self.ana_web, ruta + "?usuario_id=" + self.luis.id, {"revision": 0}).status_code, 400)
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": True}).status_code, 400)
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": 0}).status_code, 200)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/finalizar", {"revision": 0}).status_code, 409)
        self.assertTrue(self.ana_web.get("/api/carrera", base_url=self.url).json["activa"])
        self.assertIsNone(self.flota.consultar(self.luis.id))

    def test_transiciones_invalidas_no_cambian_revision(self):
        self.login(self.ana_web, "Ana")
        self.assertEqual(self.post(self.ana_web, "/api/carrera/finalizar", {"revision": 0}).status_code, 409)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).json["revision"], 0)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 1}).status_code, 409)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/estado", {"revision": 1, "estado": "parado"}).status_code, 409)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/reintentar", {"revision": 1}).status_code, 409)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).json["revision"], 1)

    def test_fallo_de_historial_permanece_pendiente_y_se_reintenta(self):
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0})
        self.tiempo = 10_000_000_000
        with patch.object(HistorialCSV, "guardar", side_effect=OSError(errno.ENOSPC, "simulado")):
            respuesta = self.post(self.ana_web, "/api/carrera/finalizar", {"revision": 1})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json["pendiente"])
        self.assertIn("No se ha podido guardar", respuesta.json["mensaje"])
        self.assertIn("No queda espacio", respuesta.json["mensaje"])
        self.assertNotIn("exportar RUTA", respuesta.json["mensaje"])
        self.assertNotIn("terminal", respuesta.json["mensaje"])
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 2}).status_code, 409)
        recuperada = self.post(self.ana_web, "/api/carrera/reintentar", {"revision": 2})
        self.assertEqual(recuperada.status_code, 200)
        self.assertFalse(recuperada.json["pendiente"])
        self.assertEqual(len(self.ana_web.get("/api/historial", base_url=self.url).json["carreras"]), 1)
    def test_tarifas_invalidas_no_comunican_inicio_exitoso(self):
        self.login(self.ana_web, "Ana")
        def tarifas_invalidas():
            raise ConfiguracionError("Tarifas de prueba inválidas")
        self.flota._crear_servicio = lambda _: ServicioCarreras(crear_carrera=tarifas_invalidas)
        respuesta = self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0})
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(respuesta.json["activa"])
        self.assertEqual(respuesta.json["revision"], 1)
        self.assertIn("No se puede iniciar", respuesta.json["error"])

    def test_reintento_fallido_informa_y_permanece_pendiente(self):
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0})
        with patch.object(HistorialCSV, "guardar", side_effect=OSError(errno.ENOSPC, "simulado")):
            self.post(self.ana_web, "/api/carrera/finalizar", {"revision": 1})
            respuesta = self.post(self.ana_web, "/api/carrera/reintentar", {"revision": 2})
        self.assertEqual(respuesta.status_code, 503)
        self.assertTrue(respuesta.json["pendiente"])
        self.assertEqual(respuesta.json["revision"], 3)
        self.assertFalse(self.post(self.ana_web, "/api/carrera/reintentar", {"revision": 3}).json["pendiente"])
    def test_accion_renueva_inactividad_pero_consulta_no(self):
        self.login(self.ana_web, "Ana")
        self.ahora += timedelta(minutes=30)
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
        self.ahora += timedelta(minutes=31)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).status_code, 200)
        self.ahora += timedelta(minutes=29)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).status_code, 401)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)

    def test_actividad_no_extiende_las_ocho_horas_maximas(self):
        self.login(self.ana_web, "Ana")
        self.assertEqual(self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
        for indice in range(9):
            self.ahora += timedelta(minutes=50)
            estado = "en movimiento" if indice % 2 == 0 else "parado"
            self.assertEqual(self.post(self.ana_web, "/api/carrera/estado",
                                     {"revision": indice + 1, "estado": estado}).status_code, 200)
        self.ahora += timedelta(minutes=30)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).status_code, 401)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)
    def test_cerrar_sesion_deja_carrera_activa_y_revoca_api(self):
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0})
        self.assertEqual(self.ana_web.post("/acceso/cerrar", base_url=self.url, headers=self.origen).status_code, 200)
        self.assertEqual(self.ana_web.get("/api/carrera", base_url=self.url).status_code, 401)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)
    def test_tarifas_de_carrera_solo_para_su_propietario(self):
        ruta = "/api/carrera/tarifas"
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url).status_code, 401)
        self.login(self.ana_web, "Ana")
        self.login(self.luis_web, "Luis")
        self.assertIsNone(self.ana_web.get(ruta, base_url=self.url).json["tarifas"])
        self.post(self.ana_web, "/api/carrera/iniciar", {"revision": 0})
        datos = self.ana_web.get(ruta, base_url=self.url).json
        self.assertEqual(datos["tarifas"], {"parado": "0.02", "en movimiento": "0.05"})
        self.assertEqual(datos["revision"], 1)
        self.assertIsNone(self.luis_web.get(ruta, base_url=self.url).json["tarifas"])
        self.assertEqual(self.luis_web.get(ruta + "?usuario=Ana", base_url=self.url).status_code, 400)
