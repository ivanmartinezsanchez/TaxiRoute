"""Integración web con archivos temporales y las reglas de fase 2."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import errno
import re
import unittest

from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas, guardar_tarifas
from taxiroute.historial import HistorialCSV
from taxiroute.registro import RegistroActividad
from taxiroute.servicio import ServicioCarreras
from taxiroute.web import crear_app


class PruebasIntegracionWeb(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.carpeta = Path(self.temporal.name)
        self.ruta = self.carpeta / "tarifas.json"
        self.ruta.write_text('{"parado":"0.02","en_movimiento":"0.05"}', encoding="utf-8")
        self.tiempo = 0
        self.fecha = datetime.now(timezone.utc)
        self.historial = HistorialCSV(self.carpeta / "historial.csv")
        self.registro = RegistroActividad(self.carpeta / "actividad.jsonl")
        self.servicio = ServicioCarreras(
            lambda: Carrera(reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha,
                            tarifas=cargar_tarifas(self.ruta)),
            historial=self.historial, registro=self.registro)
        self.app = crear_app(self.servicio, ruta_tarifas=self.ruta, modo_prueba=True)
        self.cliente = self.app.test_client()
        html = self.cliente.get("/").get_data(as_text=True)
        self.token = re.search(r'name="taxiroute-token" content="([^"]+)"', html).group(1)

    def accion(self, nombre, **datos):
        revision = self.cliente.get("/prototipo/estado").json["revision"]
        return self.cliente.post("/prototipo/accion",
            json={"accion": nombre, "revision": revision, **datos},
            headers={"X-TaxiRoute-Token": self.token})

    def test_tarifas_editadas_durante_carrera_solo_afectan_siguiente(self):
        self.accion("iniciar")
        cambio = self.accion("tarifas", tarifas={"parado": "0.03", "en_movimiento": "0.06"})
        self.assertEqual(cambio.status_code, 200)
        self.assertEqual(cambio.json["tarifas"]["parado"], "0.02")
        self.assertEqual(cambio.json["tarifas_siguientes"]["parado"], "0.03")
        self.tiempo = 10_000_000_000
        self.assertEqual(self.accion("finalizar").json["importe"], "0.20")
        self.accion("iniciar")
        self.tiempo = 20_000_000_000
        self.assertEqual(self.accion("finalizar").json["importe"], "0.30")
        filas = self.historial.consultar()
        self.assertEqual([f["tarifa_parado"] for f in filas], ["0.02", "0.03"])
        self.assertIn("tarifas_actualizadas", self.registro.ruta.read_text(encoding="utf-8"))

    def test_tarifas_invalidas_o_error_de_escritura_conservan_archivo(self):
        anterior = self.ruta.read_bytes()
        respuesta = self.accion("tarifas", tarifas={"parado": "-1", "en_movimiento": "0.05"})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.ruta.read_bytes(), anterior)
        with patch("taxiroute.configuracion.os.replace", side_effect=PermissionError("denegado")):
            respuesta = self.accion("tarifas", tarifas={"parado": "0.03", "en_movimiento": "0.05"})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(self.ruta.read_bytes(), anterior)
        self.assertEqual(list(self.carpeta.glob(".tarifas-*.tmp")), [])

    def test_historial_persistente_se_recupera_en_otra_instancia(self):
        self.accion("iniciar")
        self.tiempo = 5_000_000_000
        self.accion("finalizar")
        nuevo = crear_app(carpeta_datos=self.carpeta, ruta_tarifas=self.ruta).test_client()
        filas = nuevo.get("/prototipo/historial?filtro=todo").json["carreras"]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["importe"], "0.10")

    def test_filtro_hoy_y_historial_completo(self):
        self.fecha -= timedelta(days=2)
        self.accion("iniciar")
        self.accion("finalizar")
        self.fecha = datetime.now(timezone.utc)
        self.accion("iniciar")
        self.accion("finalizar")
        self.assertEqual(len(self.cliente.get("/prototipo/historial?filtro=todo").json["carreras"]), 2)
        self.assertEqual(len(self.cliente.get("/prototipo/historial?filtro=hoy").json["carreras"]), 1)
        self.assertEqual(self.cliente.get("/prototipo/historial?filtro=otro").status_code, 400)

    def test_error_guardado_exportacion_y_reintento_sin_duplicados(self):
        self.accion("iniciar")
        with patch.object(self.historial, "guardar", side_effect=OSError(errno.ENOSPC, "sin espacio")):
            final = self.accion("finalizar")
        self.assertTrue(final.json["pendiente"])
        self.assertIn("espacio", final.json["mensaje"])
        self.assertEqual(self.accion("iniciar").status_code, 409)
        self.assertEqual(self.accion("salir").status_code, 409)
        copia = self.carpeta / "Copia prueba.csv"
        self.assertIn("Copia creada", self.accion("exportar", ruta=str(copia)).json["mensaje"])
        contenido = copia.read_bytes()
        self.assertIn("Ya existe", self.accion("exportar", ruta=str(copia)).json["mensaje"])
        self.assertEqual(copia.read_bytes(), contenido)
        self.assertIsNotNone(self.servicio.pendiente)
        self.assertFalse(self.accion("reintentar").json["pendiente"])
        self.assertEqual(self.accion("reintentar").status_code, 409)
        self.assertEqual(len(self.historial.consultar()), 1)

    def test_registros_y_cierre_solo_sin_carrera(self):
        self.accion("iniciar")
        self.assertEqual(self.accion("salir").status_code, 409)
        self.accion("mover")
        self.accion("finalizar")
        self.assertTrue(self.accion("salir").json["cerrada"])
        eventos = self.cliente.get("/prototipo/registros").json["eventos"]
        self.assertEqual([e["evento"] for e in eventos],
            ["aplicacion_iniciada", "carrera_iniciada", "estado_cambiado",
             "carrera_finalizada", "historial_guardado", "aplicacion_finalizada"])
        self.assertEqual(self.accion("iniciar").status_code, 409)

    def test_fallo_de_registro_avisa_sin_impedir_guardado(self):
        self.accion("iniciar")
        with patch.object(self.registro.ruta.__class__, "open", side_effect=PermissionError("fallo")):
            # Solo se comprueba el registro con una operación que no escribe historial.
            respuesta = self.accion("mover")
        self.assertIn("registros", respuesta.json["aviso_registro"])
        final = self.accion("finalizar")
        self.assertFalse(final.json["pendiente"])
        self.assertEqual(len(self.historial.consultar()), 1)

    def test_historial_corrupto_no_se_sobrescribe(self):
        self.historial.ruta.write_bytes(b"fila rota")
        anterior = self.historial.ruta.read_bytes()
        self.assertEqual(self.cliente.get("/prototipo/historial").status_code, 409)
        self.accion("iniciar")
        self.assertTrue(self.accion("finalizar").json["pendiente"])
        self.assertEqual(self.historial.ruta.read_bytes(), anterior)

    def test_corregir_configuracion_ausente_permite_iniciar(self):
        self.ruta.unlink()
        respuesta = self.accion("iniciar")
        self.assertFalse(respuesta.json["activa"])
        self.assertIsNotNone(respuesta.json["error_tarifas"])
        self.accion("tarifas", tarifas={"parado": "0.02", "en_movimiento": "0.05"})
        self.assertTrue(self.accion("iniciar").json["activa"])
