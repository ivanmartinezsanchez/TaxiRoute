"""Historial de flota web con sesiones y datos ficticios."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from taxiroute.base_cuentas import conectar
from taxiroute.carrera import ResumenCarrera
from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO, crear_demo_temporal
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.web_acceso import NOMBRE_COOKIE


class PruebasHistorialFlotaWeb(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.buzon = []
        self.app = crear_demo_temporal(self.carpeta, mostrar_codigo=self.buzon.append, durable=True)
        self.app.testing = True
        self.admin, self.conductor, self.anonimo = [self.app.test_client() for _ in range(3)]
        self.url = "https://localhost"
        self.origen = {"Origin": self.url}
        self.assertEqual(self.post(self.conductor, "/acceso/conductor", {"usuario":"AnaDemo", "contrasena":CLAVE_CONDUCTOR_DEMO}).status_code, 200)
        self.assertEqual(self.post(self.admin, "/acceso/administrador", {"usuario":"AdminDemo", "contrasena":CLAVE_ADMIN_DEMO}).status_code, 200)
        self.assertEqual(self.post(self.admin, "/acceso/administrador/pin", {"codigo":self.buzon[-1]}).status_code, 200)
        with conectar(self.carpeta / "cuentas.sqlite3") as conexion:
            self.ana = conexion.execute("SELECT id FROM usuarios WHERE nombre_usuario='AnaDemo'").fetchone()[0]
            self.admin_id = conexion.execute("SELECT id FROM usuarios WHERE nombre_usuario='AdminDemo'").fetchone()[0]
        self.ruta = self.carpeta / "historial.sqlite3"
        ahora = datetime.now(timezone.utc)
        self.guardar("ana-hoy", self.ana, ahora, "1.20")
        self.guardar("admin-hoy", self.admin_id, ahora, "2.30")
        self.guardar("sin-asignar", None, ahora, "3.40")
        self.guardar("antigua", self.ana, ahora - timedelta(days=3), "4.50")

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def guardar(self, identificador, usuario, fin, importe):
        resumen = ResumenCarrera(identificador=identificador, inicio=fin-timedelta(seconds=10), fin=fin,
            segundos_parado=Decimal("10"), segundos_movimiento=Decimal("0"), tarifa_parado=Decimal("0.02"),
            tarifa_movimiento=Decimal("0.05"), importe=Decimal(importe))
        HistorialSQLite(self.ruta, usuario_id=usuario, exigir_existente=True).guardar(resumen)

    def get(self, cliente, ruta="/api/administracion/historial"):
        return cliente.get(ruta, base_url=self.url)

    def test_solo_administrador_puede_consultar(self):
        self.assertEqual(self.get(self.anonimo).status_code, 401)
        self.assertEqual(self.get(self.conductor).status_code, 403)
        respuesta = self.get(self.admin)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        self.assertEqual(self.conductor.get("/administracion", base_url=self.url).status_code, 403)
        self.assertIn("Historial de la flota", self.admin.get("/administracion", base_url=self.url).get_data(as_text=True))

    def test_hoy_todas_y_sin_propietario(self):
        hoy = self.get(self.admin).json
        self.assertEqual(hoy["periodo"], "hoy")
        self.assertEqual({f["conductor"] for f in hoy["carreras"]}, {"Ana de prueba", "Administración de prueba", "Sin asignar"})
        self.assertEqual(len(hoy["carreras"]), 3)
        todas = self.get(self.admin, "/api/administracion/historial?periodo=todas").json
        self.assertEqual(len(todas["carreras"]), 4)
        self.assertFalse(todas["hay_mas"])
        self.assertTrue(all("usuario_id" not in f for f in todas["carreras"]))
        propias = self.conductor.get("/api/historial", base_url=self.url).json["carreras"]
        self.assertEqual(len(propias), 2)

    def test_parametros_invalidos_no_se_aceptan(self):
        for ruta in ("?periodo=otro", "?usuario=Ana", "?periodo=hoy&periodo=todas", "?pagina=0", "?pagina=10001", "?pagina=1&pagina=2", "?pagina=xyz"):
            self.assertEqual(self.get(self.admin, "/api/administracion/historial" + ruta).status_code, 400)

    def test_sesion_revocada_durante_lectura_no_recibe_datos(self):
        from taxiroute.sesiones import ServicioSesiones
        token = self.admin.get_cookie(NOMBRE_COOKIE).value
        sesiones = ServicioSesiones(self.carpeta / "cuentas.sqlite3")
        verificar = HistorialSQLite._verificar
        def revocar_y_verificar(conexion, *, permitir_creacion):
            sesiones.cerrar(token)
            return verificar(conexion, permitir_creacion=permitir_creacion)
        with patch("taxiroute.web_historial_flota.HistorialSQLite._verificar", side_effect=revocar_y_verificar):
            respuesta = self.get(self.admin)
        self.assertEqual(respuesta.status_code, 401)
        self.assertNotIn("1.20", respuesta.get_data(as_text=True))

    def test_archivo_ausente_no_se_recrea(self):
        self.ruta.unlink()
        self.assertEqual(self.get(self.admin).status_code, 503)
        self.assertFalse(self.ruta.exists())

    def test_resultados_limitados_y_cuentas_no_disponibles(self):
        from contextlib import closing
        import sqlite3
        ahora = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.ruta)) as conexion:
            conexion.executemany("""INSERT INTO carreras
                (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id)
                VALUES (?, ?, ?, '1', '1', '0', '0.02', '0.05', '0.02', ?)""",
                [(f"extra-{i}", ahora, ahora, "cuenta-ya-no-disponible") for i in range(101)])
            conexion.commit()
        resultado = self.get(self.admin, "/api/administracion/historial?periodo=todas").json
        self.assertTrue(resultado["hay_mas"])
        self.assertEqual(len(resultado["carreras"]), 100)
        self.assertEqual(resultado["limite"], 100)
        self.assertTrue(all(f["conductor"] == "Cuenta no disponible" for f in resultado["carreras"]))
        self.assertFalse(resultado["hay_anterior"])
        siguiente = self.get(self.admin, "/api/administracion/historial?periodo=todas&pagina=2").json
        self.assertEqual(siguiente["pagina"], 2)
        self.assertTrue(siguiente["hay_anterior"])
        self.assertFalse(siguiente["hay_mas"])
        self.assertEqual(len(siguiente["carreras"]), 5)
