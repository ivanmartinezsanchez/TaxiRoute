"""API durable CSV con cuentas y archivos temporales, sin servidor real."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from taxiroute.api_durable_experimental import crear_api_durable_experimental
from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.sesiones import ServicioSesiones
from taxiroute.servicio import ServicioCarreras
from taxiroute.web_login import crear_acceso_conductor


class PruebasApiDurableCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.base = carpeta / "cuentas.sqlite3"
        self.historiales = carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.flota = FlotaEnMemoria(self.servicio, modo_durable_experimental=True)
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}
        self.app = self.crear_app(self.flota)
        self.ana_web = self.entrar(self.app, "Ana")
        self.luis_web = self.entrar(self.app, "Luis")

    def servicio(self, _):
        return ServicioCarreras(crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo,
                                                              fecha_actual=lambda: self.fecha))

    def crear_app(self, flota):
        app = Flask(__name__)
        app.testing = True
        app.config["DEMO_PERSISTENTE"] = True
        app.register_blueprint(crear_acceso_conductor(self.sesiones))
        app.register_blueprint(crear_api_durable_experimental(self.sesiones, flota, self.historiales,
                                                               formato_historial="csv"))
        return app

    def post(self, cliente, ruta, datos):
        return cliente.post("/api/durable" + ruta, base_url=self.url, headers=self.origen, json=datos)

    def entrar(self, app, nombre):
        cliente = app.test_client()
        respuesta = cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                                 json={"usuario": nombre, "contrasena": "Conductor123!"})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def avanzar(self, segundos):
        self.fecha += timedelta(seconds=segundos)
        self.tiempo += segundos * 1_000_000_000

    def iniciar(self, cliente, revision=0):
        return self.post(cliente, "/carrera/iniciar", {"revision": revision, "id_operacion": str(uuid4())})

    def historial(self, cliente):
        return cliente.get("/api/durable/historial", base_url=self.url)

    def test_carreras_simultaneas_y_aislamiento(self):
        self.assertEqual(self.iniciar(self.ana_web).status_code, 200)
        self.assertEqual(self.iniciar(self.luis_web).status_code, 200)
        self.avanzar(10)
        self.assertEqual(self.post(self.ana_web, "/carrera/finalizar", {"revision": 1}).status_code, 200)
        self.assertEqual(len(self.historial(self.ana_web).json["carreras"]), 1)
        self.assertEqual(self.historial(self.luis_web).json["carreras"], [])
        self.assertEqual(self.post(self.luis_web, "/carrera/finalizar", {"revision": 1}).status_code, 200)
        self.assertNotEqual(self.historial(self.ana_web).json["carreras"][0]["identificador"],
                            self.historial(self.luis_web).json["carreras"][0]["identificador"])
        self.assertEqual(self.iniciar(self.ana_web, 2).status_code, 200)

    def test_fallo_y_reintento_sin_duplicados(self):
        self.iniciar(self.ana_web)
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.liberar_cierre_csv", side_effect=OSError("simulado")):
            self.assertEqual(self.post(self.ana_web, "/carrera/finalizar", {"revision": 1}).status_code, 503)
        self.assertEqual(self.post(self.luis_web, "/carrera/reintentar", {"revision": 1}).status_code, 409)
        self.assertEqual(self.post(self.ana_web, "/carrera/reintentar", {"revision": 1}).status_code, 200)
        self.assertEqual(len(self.historial(self.ana_web).json["carreras"]), 1)

    def test_historial_ausente_o_modificado_da_error(self):
        self.iniciar(self.ana_web)
        self.avanzar(10)
        self.post(self.ana_web, "/carrera/finalizar", {"revision": 1})
        csv = self.historiales / self.ana.id / "historial.csv"
        original = csv.read_text(encoding="utf-8")
        csv.write_text(original.replace(",0.20\n", ",9.00\n"), encoding="utf-8", newline="")
        self.assertEqual(self.historial(self.ana_web).status_code, 503)
        csv.unlink()
        self.assertEqual(self.historial(self.ana_web).status_code, 503)
        self.assertFalse(csv.exists())

    def test_sesion_revocada_antes_de_conciliar_no_escribe_csv(self):
        from taxiroute.rutas_csv import _historial_de_cuenta
        from taxiroute.web_acceso import NOMBRE_COOKIE
        self.iniciar(self.ana_web)
        self.avanzar(10)
        token = self.ana_web.get_cookie(NOMBRE_COOKIE, domain="localhost").value
        def seleccionar_y_revocar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.sesiones.cerrar(token)
            return historial
        with patch("taxiroute.rutas_csv._historial_de_cuenta", side_effect=seleccionar_y_revocar):
            self.assertEqual(self.post(self.ana_web, "/carrera/finalizar", {"revision": 1}).status_code, 401)
        self.assertFalse((self.historiales / self.ana.id / "historial.csv").exists())
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_reinicio_confirma_ultima_captura_sin_cobrar_periodo_ausente(self):
        self.iniciar(self.ana_web)
        self.avanzar(10)
        cambio = {"revision": 1, "id_operacion": str(uuid4()), "estado": "en movimiento"}
        self.assertEqual(self.post(self.ana_web, "/carrera/estado", cambio).status_code, 200)
        self.avanzar(4 * 3600)
        reinicio = crear_flota_con_recuperacion(self.base, self.servicio, modo_durable_experimental=True)
        app = self.crear_app(reinicio.flota)
        cliente = self.entrar(app, "Ana")
        vista = cliente.get("/api/durable/carrera/revision-recuperacion", base_url=self.url)
        self.assertEqual(vista.status_code, 200)
        punto = vista.json["pendiente"]
        datos = {"revision": punto["revision_recuperacion"], "ultimo_control": punto["ultimo_control"],
                 "huella": punto["huella"], "confirmar": True}
        cierre = self.post(cliente, "/carrera/confirmar-recuperacion", datos)
        self.assertEqual(cierre.status_code, 200)
        filas = self.historial(cliente).json["carreras"]
        self.assertEqual((len(filas), filas[0]["fin"], filas[0]["importe"]),
                         (1, punto["ultimo_control"], punto["importe_recuperado"]))
        self.assertEqual(self.iniciar(cliente, cierre.json["revision"]).status_code, 200)

    def test_reinicio_reintenta_si_falla_liberacion_tras_guardar(self):
        self.iniciar(self.ana_web)
        self.avanzar(10)
        cambio = {"revision": 1, "id_operacion": str(uuid4()), "estado": "en movimiento"}
        self.assertEqual(self.post(self.ana_web, "/carrera/estado", cambio).status_code, 200)
        reinicio = crear_flota_con_recuperacion(self.base, self.servicio, modo_durable_experimental=True)
        app = self.crear_app(reinicio.flota)
        cliente = self.entrar(app, "Ana")
        punto = cliente.get("/api/durable/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
        datos = {"revision": punto["revision_recuperacion"], "ultimo_control": punto["ultimo_control"],
                 "huella": punto["huella"], "confirmar": True}
        with patch("taxiroute.api_durable_experimental.liberar_recuperada_csv", side_effect=OSError("simulado")):
            self.assertEqual(self.post(cliente, "/carrera/confirmar-recuperacion", datos).status_code, 503)
        self.assertEqual(self.post(cliente, "/carrera/confirmar-recuperacion", datos).status_code, 200)
        self.assertEqual(len(self.historial(cliente).json["carreras"]), 1)
