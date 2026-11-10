"""Recorrido integrado experimental con instalaciones y usuarios temporales."""
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.configuracion import guardar_tarifas
from taxiroute.cuentas import crear_cuenta
from taxiroute.preparar_csv_local import preparar_archivos_csv


class PruebasComposicionDurableCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / "instalacion")
        self.ana = crear_cuenta(self.archivos.cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.argumentos = dict(ruta_cuentas=self.archivos.cuentas, carpeta_historial=self.archivos.historiales,
            ruta_tarifas=self.archivos.tarifas, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None, durable=True)
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def cliente(self, app):
        cliente = app.test_client()
        respuesta = cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
            json={"usuario": "Ana", "contrasena": "Conductor123!"})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def test_interrumpir_revisar_guardar_y_empezar_otra_carrera(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            inicio = self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
            self.assertEqual(app.extensions["control_periodico"].ciclo(), 0)
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            self.assertEqual(cliente.get("/api/carrera", base_url=self.url).status_code, 409)
            revision = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
            datos = {"revision": revision["revision_recuperacion"], "ultimo_control": revision["ultimo_control"],
                     "huella": revision["huella"], "confirmar": True}
            cierre = self.post(cliente, "/api/carrera/confirmar-recuperacion", datos)
            self.assertEqual(cierre.status_code, 200)
            historial = cliente.get("/api/historial", base_url=self.url).json["carreras"]
            self.assertEqual(len(historial), 1)
            self.assertEqual((historial[0]["fin"], historial[0]["importe"]),
                             (revision["ultimo_control"], revision["importe_recuperado"]))
            nueva = self.post(cliente, "/api/carrera/iniciar", {"revision": cierre.json["revision"], "id_operacion": str(uuid4())})
            self.assertEqual(nueva.status_code, 200)

    def test_arranque_durable_rechaza_csv_confirmado_eliminado(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
        csv = self.archivos.historiales / self.ana.id / "historial.csv"
        csv.unlink()
        with self.assertRaises(ValueError):
            with abrir_app_csv_experimental(**self.argumentos):
                self.fail("No se debe abrir sin el historial confirmado")
        self.assertFalse(csv.exists())

    def test_modo_durable_rechaza_instalacion_sin_vinculo(self):
        cuentas = self.raiz / "antigua.sqlite3"
        tarifas = self.raiz / "tarifas.json"
        carpeta = self.raiz / "antigua"
        carpeta.mkdir()
        inicializar_base(cuentas)
        guardar_tarifas(tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
        argumentos = self.argumentos | {"ruta_cuentas": cuentas, "ruta_tarifas": tarifas, "carpeta_historial": carpeta}
        with self.assertRaisesRegex(ValueError, "vinculada"):
            with abrir_app_csv_experimental(**argumentos):
                self.fail("No se debe adoptar una instalación anterior")
