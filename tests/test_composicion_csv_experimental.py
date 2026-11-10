"""Web experimental CSV con cuentas y archivos temporales."""
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.bloqueo import HistorialOcupado
from taxiroute.composicion_csv_experimental import _historial_de_cuenta, abrir_app_csv_experimental
from taxiroute.configuracion import guardar_tarifas
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_integrada import CLAVE_CONDUCTOR_DEMO, crear_demo_temporal


class PruebasComposicionCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.cuentas = self.carpeta / "cuentas.sqlite3"
        self.tarifas = self.carpeta / "tarifas.json"
        self.historiales = self.carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.cuentas)
        guardar_tarifas(self.tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
        self.clave = "ConductorPrueba123!"
        self.ana = crear_cuenta(self.cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        self.luis = crear_cuenta(self.cuentas, nombre_usuario="Luis", nombre_visible="Luis", contrasena=self.clave)
        self.admin = crear_cuenta(self.cuentas, nombre_usuario="Admin", nombre_visible="Admin",
                                 contrasena="AdminPrueba123!", rol="administrador")
        with conectar(self.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                             (self.admin.id, "prueba@example.invalid"))
        fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.buzon = []
        self.argumentos = dict(ruta_cuentas=self.cuentas, ruta_tarifas=self.tarifas,
            carpeta_historial=self.historiales, clave_hmac=token_bytes(32),
            enviar_pin=lambda correo, codigo: self.buzon.append(codigo),
            reloj=lambda: fecha)
        self.url, self.origen = "https://localhost", {"Origin": "https://localhost"}

    def entrar(self, app, nombre):
        cliente = app.test_client()
        respuesta = cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                                 json={"usuario": nombre, "contrasena": self.clave})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def test_dos_conductores_aislados_y_csv_persistente_tras_reabrir(self):
        historico = self.historiales / "historial.csv"
        historico.write_text("histórico sin asignar\n", encoding="utf-8")
        with abrir_app_csv_experimental(**self.argumentos) as app:
            app.testing = True
            ana, luis = self.entrar(app, "Ana"), self.entrar(app, "Luis")
            self.assertEqual(self.post(ana, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(self.post(luis, "/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(self.post(ana, "/api/carrera/estado", {"revision": 1, "estado": "en movimiento"}).status_code, 200)
            self.assertEqual(luis.get("/api/carrera", base_url=self.url).json["estado"], "parado")
            admin = app.test_client()
            self.assertEqual(self.post(admin, "/acceso/administrador", {"usuario": "Admin", "contrasena": "AdminPrueba123!"}).status_code, 200)
            self.assertEqual(self.post(admin, "/acceso/administrador/pin", {"codigo": self.buzon[-1]}).status_code, 200)
            self.assertEqual(self.post(admin, "/api/tarifas", {"parado": "0.03", "en_movimiento": "0.06"}).status_code, 200)
            self.assertEqual(self.post(ana, "/api/carrera/finalizar", {"revision": 2}).status_code, 200)
            self.assertEqual(self.post(luis, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
            self.assertEqual(self.post(ana, "/api/carrera/iniciar", {"revision": 3}).status_code, 200)
            self.assertEqual(self.post(ana, "/api/carrera/finalizar", {"revision": 4}).status_code, 200)
            filas_ana = ana.get("/api/historial", base_url=self.url).json["carreras"]
            filas_luis = luis.get("/api/historial", base_url=self.url).json["carreras"]
            self.assertEqual(ana.get(f"/api/historial?usuario_id={self.luis.id}", base_url=self.url).status_code, 400)
            self.assertEqual((len(filas_ana), len(filas_luis)), (2, 1))
            self.assertNotEqual(filas_ana[0]["identificador"], filas_luis[0]["identificador"])
            self.assertEqual([fila["tarifa_parado"] for fila in filas_ana], ["0.02", "0.03"])
            self.assertEqual(filas_luis[0]["tarifa_parado"], "0.02")
            self.assertTrue((self.historiales / self.ana.id / "historial.csv").is_file())
            self.assertTrue((self.historiales / self.luis.id / "historial.csv").is_file())
        with abrir_app_csv_experimental(**self.argumentos) as app:
            self.assertEqual(len(self.entrar(app, "Ana").get("/api/historial", base_url=self.url).json["carreras"]), 2)
            self.assertEqual(len(self.entrar(app, "Luis").get("/api/historial", base_url=self.url).json["carreras"]), 1)
        self.assertEqual(historico.read_text(encoding="utf-8"), "histórico sin asignar\n")

    def test_excluye_otra_instancia_cooperativa(self):
        with abrir_app_csv_experimental(**self.argumentos):
            with self.assertRaises(HistorialOcupado):
                with abrir_app_csv_experimental(**self.argumentos):
                    self.fail("No debe haber dos instancias")

    def test_rechaza_identificador_de_ruta_y_directorio_ausente(self):
        with self.assertRaises(ValueError):
            _historial_de_cuenta(self.historiales, "../otra")
        ausente = self.carpeta / "ausente"
        with self.assertRaises(ValueError):
            with abrir_app_csv_experimental(**(self.argumentos | {"carpeta_historial": ausente})):
                pass
        self.assertFalse(ausente.exists())

    def test_rechaza_historial_dentro_de_repositorio(self):
        repositorio = self.carpeta / "repo"
        repositorio.mkdir()
        (repositorio / ".git").mkdir()
        historial = repositorio / "historial"
        historial.mkdir()
        with self.assertRaisesRegex(ValueError, "repositorio Git"):
            with abrir_app_csv_experimental(**(self.argumentos | {"carpeta_historial": historial})):
                pass
        self.assertEqual(list(historial.iterdir()), [])

    def test_rechaza_base_con_carrera_durable_sin_modificarla(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            app = crear_demo_temporal(carpeta, mostrar_codigo=lambda _: None, durable=True)
            cliente = app.test_client()
            self.assertEqual(cliente.post("/acceso/conductor", base_url=self.url, headers=self.origen,
                json={"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
            argumentos = self.argumentos | {"ruta_cuentas": carpeta / "cuentas.sqlite3", "ruta_tarifas": carpeta / "tarifas.json"}
            with self.assertRaisesRegex(ValueError, "registros durables"):
                with abrir_app_csv_experimental(**argumentos):
                    pass
            with conectar(carpeta / "cuentas.sqlite3") as conexion:
                self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 1)
            self.assertEqual(self.post(cliente, "/api/carrera/finalizar", {"revision": 1}).status_code, 200)
            with self.assertRaisesRegex(ValueError, "registros durables"):
                with abrir_app_csv_experimental(**argumentos):
                    pass


if __name__ == "__main__":
    unittest.main()
