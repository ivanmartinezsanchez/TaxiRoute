"""Recorrido integral de acción interrumpida con almacenamiento CSV ficticio."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from taxiroute.base_cuentas import conectar
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.configuracion import guardar_tarifas
from taxiroute.cuentas import crear_cuenta
from taxiroute.preparar_csv_local import preparar_archivos_csv


class PruebasRecorridoIntencionCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.archivos = preparar_archivos_csv(Path(temporal.name) / "datos")
        self.ana = crear_cuenta(self.archivos.cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.ahora = datetime.now(timezone.utc)
        self.argumentos = dict(ruta_cuentas=self.archivos.cuentas, carpeta_historial=self.archivos.historiales,
            ruta_tarifas=self.archivos.tarifas, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None,
            durable=True, reloj=lambda: self.ahora)
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}

    def cliente(self, app):
        app.testing = True
        cliente = app.test_client()
        respuesta = self.post(cliente, "/acceso/conductor", {"usuario": "Ana", "contrasena": "Conductor123!"})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def get(self, cliente, ruta):
        return cliente.get(ruta, base_url=self.url)

    def test_movimiento_interrumpido_revision_cierre_y_nueva_carrera(self):
        id_movimiento = str(uuid4())
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            inicio = self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
            with patch("taxiroute.flota.FlotaEnMemoria.aplicar_intencion_experimental", side_effect=OSError("corte simulado")):
                interrumpida = self.post(cliente, "/api/carrera/estado", {"revision": 1, "id_operacion": id_movimiento, "estado": "en movimiento"})
            self.assertEqual(interrumpida.status_code, 503)
            pendiente = self.get(cliente, "/api/recuperacion/pendiente").json
            self.assertEqual(pendiente["intencion"]["accion"], "mover")
            ultimo = pendiente["pendiente"]["ultimo_control"]
            self.assertEqual(pendiente["pendiente"]["tarifa_parado"], "0.02")
            self.assertEqual(pendiente["pendiente"]["tarifa_movimiento"], "0.05")
        guardar_tarifas(self.archivos.tarifas, {"parado": "0.07", "en_movimiento": "0.09"})
        self.ahora += timedelta(hours=4)
        with abrir_app_csv_experimental(**self.argumentos) as app:
            cliente = self.cliente(app)
            self.assertEqual(self.get(cliente, "/api/carrera").status_code, 409)
            pendiente = self.get(cliente, "/api/recuperacion/pendiente").json
            revision = self.post(cliente, "/api/recuperacion/intencion/confirmar",
                                 {"huella": pendiente["intencion"]["huella"], "confirmar": True})
            self.assertEqual((revision.status_code, revision.json["resultado"]), (200, "carrera_pendiente"))
            revisado = self.get(cliente, "/api/carrera/revision-recuperacion")
            self.assertEqual(revisado.status_code, 200)
            captura = revisado.json["pendiente"]
            self.assertEqual(captura["ultimo_control"], ultimo)
            cierre = self.post(cliente, "/api/carrera/confirmar-recuperacion", {
                "revision": captura["revision_recuperacion"], "ultimo_control": captura["ultimo_control"],
                "huella": captura["huella"], "confirmar": True})
            self.assertEqual(cierre.status_code, 200)
            historial = self.get(cliente, "/api/historial").json["carreras"]
            self.assertEqual(len(historial), 1)
            self.assertEqual(historial[0]["fin"], ultimo)
            self.assertEqual(historial[0]["importe"], captura["importe_recuperado"])
            self.assertEqual((historial[0]["tarifa_parado"], historial[0]["tarifa_movimiento"]), ("0.02", "0.05"))
            self.assertEqual(historial[0]["segundos_movimiento"], "0")
            nueva = self.post(cliente, "/api/carrera/iniciar", {"revision": cierre.json["revision"], "id_operacion": str(uuid4())})
            self.assertEqual(nueva.status_code, 200)
            tarifas = self.get(cliente, "/api/carrera/tarifas").json["tarifas"]
            self.assertEqual(tarifas, {"parado": "0.07", "en movimiento": "0.09"})
            repetida = self.post(cliente, "/api/carrera/estado", {"revision": 1, "id_operacion": id_movimiento, "estado": "en movimiento"})
            self.assertEqual(repetida.status_code, 409)
            self.assertEqual(len(self.get(cliente, "/api/historial").json["carreras"]), 1)
