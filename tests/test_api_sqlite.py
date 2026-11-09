"""Integración aislada de API y un historial SQLite compartido por propietario."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flask import Flask
from taxiroute.api_flota import crear_api_flota
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_login import crear_acceso_conductor


class PruebasApiSQLite(unittest.TestCase):
    def test_historial_relacional_compartido_filtra_por_sesion_y_persiste(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            cuentas = carpeta / "cuentas.sqlite3"
            historial = carpeta / "historial.sqlite3"
            inicializar_base(cuentas)
            ana = crear_cuenta(cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
            luis = crear_cuenta(cuentas, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
            with conectar(cuentas) as conexion:
                conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            sesiones = ServicioSesiones(cuentas, reloj=lambda: datetime(2026, 11, 8, tzinfo=timezone.utc))
            tiempo = [0]
            def historial_por_usuario(usuario):
                return HistorialSQLite(historial, usuario_id=usuario)
            flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(
                crear_carrera=lambda: Carrera(reloj=lambda: tiempo[0]), historial=historial_por_usuario(usuario)))
            app = Flask(__name__)
            app.config.update(TESTING=True, TRUSTED_HOSTS=["localhost"], MAX_CONTENT_LENGTH=4096)
            app.register_blueprint(crear_acceso_conductor(sesiones))
            app.register_blueprint(crear_api_flota(sesiones, flota, historial_por_usuario))
            cliente_ana, cliente_luis = app.test_client(), app.test_client()
            url, origen = "https://localhost", {"Origin": "https://localhost"}
            for cliente, nombre in ((cliente_ana, "Ana"), (cliente_luis, "Luis")):
                r = cliente.post("/acceso/conductor", base_url=url, headers=origen,
                    json={"usuario": nombre, "contrasena": "ConductorPrueba123!"})
                self.assertEqual(r.status_code, 200)
            self.assertEqual(cliente_ana.post("/api/carrera/iniciar", base_url=url,
                headers=origen, json={"revision": 0}).status_code, 200)
            tiempo[0] = 10_000_000_000
            final = cliente_ana.post("/api/carrera/finalizar", base_url=url,
                headers=origen, json={"revision": 1})
            self.assertEqual(final.status_code, 200)
            self.assertFalse(final.json["pendiente"])
            ana_filas = cliente_ana.get("/api/historial", base_url=url).json["carreras"]
            self.assertEqual([x["importe"] for x in ana_filas], ["0.20"])
            self.assertEqual(cliente_luis.get("/api/historial", base_url=url).json["carreras"], [])
            self.assertEqual(HistorialSQLite(historial, usuario_id=ana.id).consultar(), ana_filas)
            self.assertEqual(HistorialSQLite(historial, usuario_id=luis.id).consultar(), [])
            self.assertEqual(HistorialSQLite(historial).consultar(), [])