"""API durable aislada: solo bases y cuentas temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from flask import Flask
from taxiroute.api_durable_experimental import crear_api_durable_experimental
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_login import crear_acceso_conductor


class PruebasApiDurable(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        self.historial = Path(temporal.name) / "historial.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="FicticiaLarga123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="FicticiaLarga123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        HistorialSQLite(self.historial, exigir_existente=True).inicializar()
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(crear_carrera=lambda: Carrera(
            reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)), modo_durable_experimental=True)
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(crear_acceso_conductor(self.sesiones))
        app.register_blueprint(crear_api_durable_experimental(self.sesiones, self.flota, self.historial))
        self.ana_web, self.luis_web = app.test_client(), app.test_client()
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}

    def login(self, cliente, usuario):
        return self.post(cliente, "/acceso/conductor", {"usuario": usuario, "contrasena": "FicticiaLarga123!"})

    def post(self, cliente, ruta, datos, headers=None):
        return cliente.post(ruta, base_url=self.url, headers=self.origen if headers is None else headers, json=datos)

    def avanzar(self, segundos):
        self.tiempo += segundos * 1_000_000_000
        self.fecha += timedelta(seconds=segundos)

    def test_dos_carreras_e_identificador_repetido(self):
        self.assertEqual(self.login(self.ana_web, "Ana").status_code, 200)
        inicio = {"revision": 0, "id_operacion": str(uuid4())}
        ruta = "/api/durable/carrera/iniciar"
        self.assertEqual(self.post(self.ana_web, ruta, inicio).json["revision"], 1)
        self.assertTrue(self.post(self.ana_web, ruta, inicio).json["repetida"])
        self.avanzar(10)
        cierre = self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        self.assertEqual((cierre.status_code, cierre.json["importe"], cierre.json["revision"]), (200, "0.20", 2))
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": 2, "id_operacion": str(uuid4())}).json["revision"], 3)
        self.avanzar(5)
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 3}).status_code, 200)
        filas = HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar()
        self.assertEqual([fila["importe"] for fila in filas], ["0.20", "0.10"])

    def test_aislamiento_y_restricciones(self):
        ruta = "/api/durable/carrera/iniciar"
        datos = {"revision": 0, "id_operacion": str(uuid4())}
        self.assertEqual(self.post(self.ana_web, ruta, datos).status_code, 401)
        self.login(self.ana_web, "Ana")
        self.login(self.luis_web, "Luis")
        self.assertEqual(self.post(self.ana_web, ruta, datos, headers={}).status_code, 403)
        self.assertEqual(self.post(self.ana_web, ruta, {**datos, "usuario_id": self.luis.id}).status_code, 400)
        self.assertEqual(self.post(self.ana_web, ruta, datos).status_code, 200)
        self.assertFalse(self.luis_web.get("/api/durable/carrera", base_url=self.url).json["activa"])
        self.assertEqual(self.post(self.luis_web, "/api/durable/carrera/finalizar", {"revision": 1}).status_code, 409)
        self.assertTrue(self.ana_web.get("/api/durable/carrera", base_url=self.url).json["activa"])

    def test_cambio_y_peticion_desactualizada(self):
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        cambio = {"revision": 1, "id_operacion": str(uuid4()), "estado": "en movimiento"}
        self.avanzar(5)
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/estado", cambio).json["revision"], 2)
        self.assertTrue(self.post(self.ana_web, "/api/durable/carrera/estado", cambio).json["repetida"])
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1}).status_code, 409)
        self.avanzar(10)
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 2}).json["importe"], "0.60")

    def test_error_guardado_bloquea_y_reinicio_conserva_pendiente(self):
        from unittest.mock import patch
        from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.conciliar_pendiente_sqlite", side_effect=OSError("simulado")):
            respuesta = self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        self.assertEqual(respuesta.status_code, 503)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.ana_web.get("/api/durable/carrera", base_url=self.url).status_code, 409)
        self.assertEqual(HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar(), [])
        reinicio = crear_flota_con_recuperacion(self.base, lambda _: ServicioCarreras(), modo_durable_experimental=True)
        self.assertTrue(reinicio.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(reinicio.pendientes[0].estado, "guardado_pendiente")

    def test_corte_durante_carrera_bloquea_al_reiniciar(self):
        from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        reinicio = crear_flota_con_recuperacion(self.base, lambda _: ServicioCarreras(), modo_durable_experimental=True)
        self.assertTrue(reinicio.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(reinicio.pendientes[0].estado, "activa")
        self.assertFalse(reinicio.flota.requiere_reconciliacion(self.luis.id))

    def test_solo_las_acciones_renuevan_inactividad(self):
        self.login(self.ana_web, "Ana")
        self.fecha += timedelta(minutes=30)
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
        self.fecha += timedelta(minutes=31)
        self.assertEqual(self.ana_web.get("/api/durable/carrera", base_url=self.url).status_code, 200)
        self.fecha += timedelta(minutes=29)
        self.assertEqual(self.ana_web.get("/api/durable/carrera", base_url=self.url).status_code, 401)

    def test_interacciones_no_superan_las_ocho_horas(self):
        self.login(self.ana_web, "Ana")
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
        for indice in range(9):
            self.avanzar(50 * 60)
            estado = "en movimiento" if indice % 2 == 0 else "parado"
            datos = {"revision": indice + 1, "id_operacion": str(uuid4()), "estado": estado}
            self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/estado", datos).status_code, 200)
        self.avanzar(30 * 60)
        self.assertEqual(self.ana_web.get("/api/durable/carrera", base_url=self.url).status_code, 401)

    def test_reintenta_solo_su_guardado_sin_duplicar(self):
        from unittest.mock import patch
        self.login(self.ana_web, "Ana")
        self.login(self.luis_web, "Luis")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.conciliar_pendiente_sqlite", side_effect=OSError("simulado")):
            respuesta = self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        self.assertEqual(respuesta.status_code, 503)
        ruta = "/api/durable/carrera/reintentar"
        info = self.ana_web.get("/api/durable/carrera/cierre-pendiente", base_url=self.url).json["pendiente"]
        self.assertTrue(info["reintento_disponible"])
        self.assertEqual((info["estado"], info["revision_reintento"]), ("guardado_pendiente", 1))
        self.assertIsNone(self.luis_web.get("/api/durable/carrera/cierre-pendiente", base_url=self.url).json["pendiente"])
        self.assertEqual(self.post(self.luis_web, ruta, {"revision": 1}).status_code, 409)
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": 0}).status_code, 409)
        respuesta = self.post(self.ana_web, ruta, {"revision": 1})
        self.assertEqual((respuesta.status_code, respuesta.json["revision"]), (200, 2))
        self.assertEqual(self.post(self.ana_web, ruta, {"revision": 1}).status_code, 409)
        self.assertEqual(len(HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar()), 1)

    def test_copia_pendiente_aislada_sin_modificar_estado(self):
        import csv
        import io
        from unittest.mock import patch
        ruta = "/api/durable/carrera/copia-pendiente"
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url).status_code, 401)
        self.login(self.ana_web, "Ana")
        self.login(self.luis_web, "Luis")
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url).status_code, 409)
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.conciliar_pendiente_sqlite", side_effect=OSError("simulado")):
            self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1}).status_code, 503)
        self.assertEqual(self.luis_web.get(ruta, base_url=self.url).status_code, 409)
        self.assertEqual(self.ana_web.get(ruta + "?usuario_id=" + str(self.luis.id), base_url=self.url).status_code, 400)
        self.assertEqual(self.ana_web.get(ruta, base_url="http://localhost").status_code, 400)
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url, headers={"Origin":"https://ajeno.test"}).status_code, 403)
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url, headers={"Sec-Fetch-Site":"cross-site"}).status_code, 403)
        anterior = self.ana_web.get("/api/durable/carrera/cierre-pendiente", base_url=self.url).json
        respuesta = self.ana_web.get(ruta, base_url=self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("attachment", respuesta.headers["Content-Disposition"])
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        filas = list(csv.DictReader(io.StringIO(respuesta.get_data(as_text=True))))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["importe"], "0.20")
        self.assertTrue(filas[0]["identificador"])
        self.assertEqual(self.ana_web.get("/api/durable/carrera/cierre-pendiente", base_url=self.url).json, anterior)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar(), [])
        self.fecha += timedelta(hours=1)
        self.assertEqual(self.ana_web.get(ruta, base_url=self.url).status_code, 401)

    def test_copia_pendiente_error_y_validacion_de_integridad(self):
        from unittest.mock import patch
        ruta = "/api/durable/carrera/copia-pendiente"
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.conciliar_pendiente_sqlite", side_effect=OSError("simulado")):
            self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        with patch("taxiroute.conciliacion_csv._leer", side_effect=OSError("ruta privada")):
            respuesta = self.ana_web.get(ruta, base_url=self.url)
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("ruta privada", respuesta.get_data(as_text=True))
        from taxiroute.conciliacion_csv import _leer
        def alterada(conexion, usuario, carrera):
            datos, estado, resumen = _leer(conexion, usuario, carrera)
            from dataclasses import replace
            return datos, estado, replace(resumen, importe="999.00")
        with patch("taxiroute.conciliacion_csv._leer", side_effect=alterada):
            self.assertEqual(self.ana_web.get(ruta, base_url=self.url).status_code, 409)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_reintenta_despues_de_guardar_sin_duplicados(self):
        from unittest.mock import patch
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(10)
        with patch("taxiroute.api_durable_experimental.liberar_cierre_conciliado", side_effect=OSError("simulado")):
            respuesta = self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(len(HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar()), 1)
        info = self.ana_web.get("/api/durable/carrera/cierre-pendiente", base_url=self.url).json["pendiente"]
        self.assertEqual((info["estado"], info["revision_reintento"]), ("guardada", 1))
        self.assertEqual(self.post(self.ana_web, "/api/durable/carrera/reintentar", {"revision": 1}).status_code, 200)
        self.assertEqual(len(HistorialSQLite(self.historial, usuario_id=self.ana.id, exigir_existente=True).consultar()), 1)

    def test_error_de_espacio_informa_sin_mostrar_rutas(self):
        import errno
        from unittest.mock import patch
        self.login(self.ana_web, "Ana")
        self.post(self.ana_web, "/api/durable/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
        self.avanzar(5)
        with patch("taxiroute.api_durable_experimental.conciliar_pendiente_sqlite", side_effect=OSError(errno.ENOSPC, "ruta privada simulada")):
            respuesta = self.post(self.ana_web, "/api/durable/carrera/finalizar", {"revision": 1})
        self.assertEqual(respuesta.status_code, 503)
        self.assertIn("No queda espacio", respuesta.json["error"])
        self.assertNotIn("ruta privada", respuesta.get_data(as_text=True))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
