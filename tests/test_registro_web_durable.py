"""Registro durable con cuentas y archivos temporales."""
import json
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_integrada import crear_demo_temporal
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.registro_web_durable import RegistroWebDurable


class PruebasRegistroWeb(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / "datos")
        self.clave = "Conductor123!"
        self.ana = crear_cuenta(self.archivos.cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.argumentos = dict(ruta_cuentas=self.archivos.cuentas, carpeta_historial=self.archivos.historiales,
            ruta_tarifas=self.archivos.tarifas, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None, durable=True)
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}
        self.log = self.archivos.carpeta / "actividad.jsonl"

    def cliente(self, app):
        cliente = app.test_client()
        respuesta = cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                                 json={"usuario": "Ana", "contrasena": self.clave})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def leer(self):
        return [json.loads(linea) for linea in self.log.read_text(encoding="utf-8").splitlines()]

    def test_csv_eventos_confirmados_reintento_y_ciclo_de_vida(self):
        identificador = str(uuid4())
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            datos = {"revision": 0, "id_operacion": identificador}
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", datos).status_code, 200)
            self.assertTrue(self.post(cliente, "/api/carrera/iniciar", datos).json["repetida"])
            self.assertEqual(self.post(cliente, "/api/carrera/estado", {"revision": 1, "id_operacion": str(uuid4()), "estado": "en movimiento"}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 2}).status_code, 200)
        entradas = self.leer()
        self.assertEqual([e["evento"] for e in entradas], ["aplicacion_iniciada", "carrera_iniciada", "estado_cambiado", "carrera_finalizada", "aplicacion_finalizada"])
        self.assertEqual(entradas[2]["estado"], "en movimiento")
        contenido = self.log.read_text(encoding="utf-8")
        for secreto in (self.clave, self.ana.id, identificador, "Ana"):
            self.assertNotIn(secreto, contenido)

    def test_fallo_anterior_al_commit_no_registra_inicio(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            with patch("taxiroute.flota.FlotaEnMemoria.aplicar_intencion_experimental", side_effect=OSError("ruta privada")):
                respuesta = self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual(respuesta.status_code, 503)
        self.assertEqual([e["evento"] for e in self.leer()], ["aplicacion_iniciada", "error_operacion", "aplicacion_finalizada"])
        self.assertNotIn("ruta privada", self.log.read_text(encoding="utf-8"))

    def test_fallo_del_registro_avisa_y_no_impide_carrera(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            with patch.object(app.extensions["registro_actividad"], "registrar", side_effect=OSError("detalle secreto")):
                respuesta = self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn("Aviso", respuesta.json["mensaje"])
            self.assertNotIn("detalle secreto", str(respuesta.json))
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)

    def test_csv_no_durable_conserva_eventos_de_fase_anterior(self):
        with abrir_app_csv_experimental(**(self.argumentos | {"durable": False})) as app:
            cliente = self.cliente(app)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/estado", {"revision": 1, "estado": "en movimiento"}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 2}).status_code, 200)
        self.assertEqual([e["evento"] for e in self.leer()], ["aplicacion_iniciada", "carrera_iniciada", "estado_cambiado", "carrera_finalizada", "historial_guardado", "aplicacion_finalizada"])

    def test_repetir_confirmacion_recuperada_no_anade_segundo_evento(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            captura = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
            datos = {"revision": captura["revision_recuperacion"], "ultimo_control": captura["ultimo_control"], "huella": captura["huella"], "confirmar": True}
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 200)
        self.assertEqual(sum(e["evento"] == "carrera_recuperada" for e in self.leer()), 1)

    def test_rechaza_registro_en_repositorio_o_evento_no_permitido(self):
        repo = self.raiz / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        ruta = repo / "actividad.jsonl"
        registro = RegistroWebDurable(ruta)
        self.assertIsNotNone(registro.registrar("carrera_iniciada"))
        self.assertIsNotNone(registro.registrar("secreto-no-permitido"))
        self.assertFalse(ruta.exists())

    def test_sqlite_registra_ciclo_de_vida_y_carrera(self):
        carpeta = self.raiz / "sqlite"
        carpeta.mkdir()
        crear_demo_temporal(carpeta, mostrar_codigo=lambda _: None, durable=True)
        argumentos = dict(ruta_cuentas=carpeta / "cuentas.sqlite3", ruta_historial=carpeta / "historial.sqlite3",
                          ruta_tarifas=carpeta / "tarifas.json", clave_hmac=token_bytes(32), enviar_pin=lambda *_: None)
        with abrir_app_experimental(**argumentos) as app:
            cliente = app.test_client()
            self.assertEqual(self.post(cliente, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": "ConductorDemo123!"}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
        entradas = [json.loads(x) for x in (carpeta / "actividad.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["evento"] for e in entradas], ["aplicacion_iniciada", "carrera_iniciada", "carrera_finalizada", "aplicacion_finalizada"])
