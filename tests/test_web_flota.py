"""Consulta experimental de flota vinculada a sesiones verificadas."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web import crear_app


class PruebasSesionYFlota(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(ruta)
        self.clave = "ConductorPrueba123!"
        self.ana = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        self.luis = crear_cuenta(ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena=self.clave)
        with conectar(ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.ahora = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(ruta, reloj=lambda: self.ahora)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo)))
        self.app = crear_app(ServicioCarreras(), modo_prueba=True,
                             sesiones_prueba=self.sesiones, flota_prueba=self.flota)
        self.app.testing = True
        self.cliente_ana = self.app.test_client()
        self.cliente_luis = self.app.test_client()
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}

    def login(self, cliente, usuario):
        return cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                            json={"usuario": usuario, "contrasena": self.clave})

    def estado(self, cliente, sufijo=""):
        return cliente.get("/prototipo/estado" + sufijo, base_url=self.url)

    def test_cada_sesion_solo_ve_su_carrera(self):
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.flota.ejecutar(self.luis.id, "iniciar")
        self.tiempo = 10_000_000_000
        self.flota.ejecutar(self.ana.id, "mover")
        self.tiempo = 20_000_000_000
        self.assertEqual(self.estado(self.cliente_ana).status_code, 401)
        self.assertEqual(self.login(self.cliente_ana, "Ana").status_code, 200)
        self.assertEqual(self.login(self.cliente_luis, "Luis").status_code, 200)
        ana = self.estado(self.cliente_ana).json
        luis = self.estado(self.cliente_luis).json
        self.assertEqual((ana["estado"], ana["importe"]), ("en movimiento", "0.70"))
        self.assertEqual((luis["estado"], luis["importe"]), ("parado", "0.40"))
        self.assertEqual(self.estado(self.cliente_ana, "?usuario_id=" + self.luis.id).status_code, 400)
        self.assertEqual(self.estado(self.cliente_luis, "?usuario_id=" + self.ana.id).status_code, 400)
        self.assertEqual(self.cliente_ana.post("/prototipo/accion", base_url=self.url).status_code, 404)

    def test_cerrar_sesion_no_interrumpe_carreras_ni_la_otra_sesion(self):
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.flota.ejecutar(self.luis.id, "iniciar")
        self.login(self.cliente_ana, "Ana")
        self.login(self.cliente_luis, "Luis")
        self.assertEqual(self.cliente_ana.post("/acceso/cerrar", base_url=self.url,
                                              headers=self.origen).status_code, 200)
        self.assertEqual(self.estado(self.cliente_ana).status_code, 401)
        self.assertTrue(self.estado(self.cliente_luis).json["activa"])
        self.assertTrue(self.flota.consultar(self.ana.id).activa)
        self.assertTrue(self.flota.consultar(self.luis.id).activa)

    def test_ninguna_carrera_y_sesion_caducada(self):
        self.login(self.cliente_ana, "Ana")
        self.assertEqual(self.estado(self.cliente_ana).json,
                         {"activa": False, "estado": "sin carrera activa", "importe": None, "pendiente": False, "revision": 0})
        self.ahora += timedelta(hours=1)
        self.assertEqual(self.estado(self.cliente_ana).status_code, 401)

    def accion(self, cliente, accion, *, headers=None, sufijo="", extra=None):
        estado_actual = self.estado(cliente)
        revision = estado_actual.json["revision"] if estado_actual.status_code == 200 else 0
        datos = {"accion": accion, "revision": revision}
        if extra:
            datos.update(extra)
        return cliente.post("/prueba/flota/accion" + sufijo, base_url=self.url,
                           headers=self.origen if headers is None else headers, json=datos)

    def test_dos_conductores_actuan_en_sus_propias_carreras(self):
        self.login(self.cliente_ana, "Ana")
        self.login(self.cliente_luis, "Luis")
        self.assertTrue(self.accion(self.cliente_ana, "iniciar").json["activa"])
        self.assertTrue(self.accion(self.cliente_luis, "iniciar").json["activa"])
        self.tiempo = 10_000_000_000
        self.assertEqual(self.accion(self.cliente_ana, "mover").json["estado"], "en movimiento")
        self.tiempo = 20_000_000_000
        self.assertEqual(self.accion(self.cliente_ana, "parar").json["importe"], "0.70")
        self.assertEqual(self.estado(self.cliente_luis).json["importe"], "0.40")
        self.assertEqual(self.accion(self.cliente_ana, "finalizar").json["importe"], "0.70")
        self.assertTrue(self.estado(self.cliente_luis).json["activa"])
        self.assertEqual(self.accion(self.cliente_luis, "finalizar").json["importe"], "0.40")

    def test_peticion_no_autorizada_no_modifica_carreras(self):
        self.assertEqual(self.accion(self.cliente_ana, "iniciar").status_code, 401)
        self.login(self.cliente_ana, "Ana")
        self.flota.ejecutar(self.luis.id, "iniciar")
        self.assertEqual(self.accion(self.cliente_ana, "finalizar", extra={"usuario_id": self.luis.id}).status_code, 400)
        self.assertEqual(self.accion(self.cliente_ana, "finalizar", sufijo="?usuario_id=" + self.luis.id).status_code, 400)
        for headers in ({}, {"Origin": "https://otro.example"},
                        {"Origin": self.url, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.accion(self.cliente_ana, "iniciar", headers=headers).status_code, 403)
        for comando in (None, [], "exportar", "tarifas"):
            self.assertEqual(self.accion(self.cliente_ana, comando).status_code, 400)
        self.assertEqual(self.cliente_ana.post("/prueba/flota/accion", base_url=self.url,
                         headers=self.origen, data="accion=iniciar").status_code, 415)
        self.assertIsNone(self.flota.consultar(self.ana.id))
        self.assertTrue(self.flota.consultar(self.luis.id).activa)

    def test_sesion_caducada_no_puede_actuar(self):
        self.login(self.cliente_ana, "Ana")
        self.assertTrue(self.accion(self.cliente_ana, "iniciar").json["activa"])
        self.ahora += timedelta(hours=1)
        self.assertEqual(self.accion(self.cliente_ana, "finalizar").status_code, 401)
        self.assertTrue(self.flota.consultar(self.ana.id).activa)

    def test_revision_antigua_no_finaliza_una_carrera_posterior(self):
        self.login(self.cliente_ana, "Ana")
        self.login(self.cliente_luis, "Luis")
        self.assertEqual(self.accion(self.cliente_ana, "iniciar").json["revision"], 1)
        self.assertEqual(self.accion(self.cliente_luis, "iniciar").json["revision"], 1)
        anterior = self.estado(self.cliente_ana).json["revision"]
        self.assertEqual(self.accion(self.cliente_ana, "finalizar").json["revision"], 2)
        self.assertEqual(self.accion(self.cliente_ana, "iniciar").json["revision"], 3)
        respuesta = self.accion(self.cliente_ana, "finalizar", extra={"revision": anterior})
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn("Actualiza", respuesta.json["error"])
        self.assertTrue(self.estado(self.cliente_ana).json["activa"])
        self.assertEqual(self.estado(self.cliente_ana).json["revision"], 3)
        self.assertEqual(self.estado(self.cliente_luis).json["revision"], 1)
        self.assertTrue(self.estado(self.cliente_luis).json["activa"])

    def test_revision_invalida_y_duplicada(self):
        self.login(self.cliente_ana, "Ana")
        for revision in (True, -1, "0", None):
            self.assertEqual(self.accion(self.cliente_ana, "iniciar", extra={"revision": revision}).status_code, 400)
        sin_revision = self.cliente_ana.post("/prueba/flota/accion", base_url=self.url,
                            headers=self.origen, json={"accion": "iniciar"})
        self.assertEqual(sin_revision.status_code, 400)
        self.assertEqual(self.accion(self.cliente_ana, "iniciar").status_code, 200)
        self.assertEqual(self.accion(self.cliente_ana, "iniciar", extra={"revision": 0}).status_code, 409)
        self.assertEqual(self.estado(self.cliente_ana).json["revision"], 1)
    def test_revocada_tras_primera_comprobacion_no_ejecuta_accion(self):
        self.login(self.cliente_ana, "Ana")
        token = self.cliente_ana.get_cookie("taxiroute_sesion").value
        metodo_real = self.sesiones.ejecutar_con_sesion_vigente
        def cerrar_antes_de_la_segunda_comprobacion(*args, **kwargs):
            self.sesiones.cerrar(token)
            return metodo_real(*args, **kwargs)
        with patch.object(self.sesiones, "ejecutar_con_sesion_vigente",
                          side_effect=cerrar_antes_de_la_segunda_comprobacion):
            respuesta = self.accion(self.cliente_ana, "iniciar")
        self.assertEqual(respuesta.status_code, 401)
        self.assertIsNone(self.flota.consultar(self.ana.id))
    def test_flota_no_se_puede_activar_sin_sesiones(self):
        with self.assertRaises(ValueError):
            crear_app(ServicioCarreras(), modo_prueba=True, flota_prueba=self.flota)