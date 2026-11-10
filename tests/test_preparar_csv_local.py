"""Preparación experimental solo en directorios temporales."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.configuracion import cargar_tarifas
from taxiroute.configurar_admin_local import main as configurar_administrador
from taxiroute.preparar_csv_local import main, preparar_archivos_csv
from taxiroute.vinculo_historial import MARCADOR_HISTORIAL


class TerminalFicticia(StringIO):
    def isatty(self):
        return True


class PruebasPreparacionCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name) / "taxiroute-prueba"

    def test_crea_archivos_vacios_sin_cuentas_y_puede_montar_web(self):
        archivos = preparar_archivos_csv(self.carpeta)
        self.assertTrue(archivos.cuentas.is_file())
        self.assertTrue(archivos.tarifas.is_file())
        self.assertEqual([ruta.name for ruta in archivos.historiales.iterdir()], [MARCADOR_HISTORIAL])
        self.assertTrue((archivos.carpeta / "PREPARACION_EXPERIMENTAL.txt").is_file())
        self.assertEqual({estado.value: str(tarifa) for estado, tarifa in cargar_tarifas(archivos.tarifas).items()},
                         {"parado": "0.02", "en movimiento": "0.05"})
        with conectar(archivos.cuentas) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
        with abrir_app_csv_experimental(ruta_cuentas=archivos.cuentas, ruta_tarifas=archivos.tarifas,
            carpeta_historial=archivos.historiales, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None) as app:
            self.assertEqual(app.test_client().get("/acceso", base_url="https://localhost").status_code, 200)

    def test_no_sobrescribe_carpeta_existente(self):
        self.carpeta.mkdir()
        archivo = self.carpeta / "conservar.txt"
        archivo.write_text("conservar", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            preparar_archivos_csv(self.carpeta)
        self.assertEqual(archivo.read_text(encoding="utf-8"), "conservar")
        self.assertEqual([x.name for x in self.carpeta.iterdir()], ["conservar.txt"])

    def test_rechaza_repositorio_y_padre_inexistente(self):
        repo = self.carpeta.parent / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "repositorio Git"):
            preparar_archivos_csv(repo / "datos")
        self.assertFalse((repo / "datos").exists())
        with self.assertRaises(FileNotFoundError):
            preparar_archivos_csv(self.carpeta / "hija")
        self.assertFalse(self.carpeta.exists())

    def test_error_de_preparacion_conserva_carpeta_incompleta_sin_marcar_completa(self):
        with patch("taxiroute.preparar_csv_local.guardar_tarifas", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                preparar_archivos_csv(self.carpeta)
        self.assertTrue(self.carpeta.is_dir())
        self.assertTrue((self.carpeta / "cuentas.sqlite3").is_file())
        self.assertFalse((self.carpeta / "PREPARACION_EXPERIMENTAL.txt").exists())
        with self.assertRaises(FileExistsError):
            preparar_archivos_csv(self.carpeta)

    def test_dos_preparaciones_concurrentes_no_reutilizan_carpeta(self):
        def intentar(_):
            try:
                preparar_archivos_csv(self.carpeta)
                return True
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            self.assertEqual(sum(ejecutor.map(intentar, (1, 2))), 1)
        self.assertTrue((self.carpeta / "PREPARACION_EXPERIMENTAL.txt").is_file())

    def test_comando_no_sustituye_archivo_existente(self):
        self.carpeta.write_text("conservar", encoding="utf-8")
        errores, salida = StringIO(), StringIO()
        with redirect_stderr(errores), redirect_stdout(salida):
            self.assertEqual(main(["--carpeta", str(self.carpeta)]), 1)
        self.assertEqual(self.carpeta.read_text(encoding="utf-8"), "conservar")
        self.assertIn("no se ha sustituido", errores.getvalue())

    def test_alta_admin_cambio_clave_carrera_csv_y_reapertura(self):
        archivos = preparar_archivos_csv(self.carpeta)
        clave_temporal, clave_nueva = "TemporalPrueba123!", "NuevaPrueba456!"
        salida, errores = TerminalFicticia(), StringIO()
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), \
                patch("builtins.input", side_effect=["Admin", "Prueba", "prueba@example.invalid", "GUARDADOS"]), \
                patch("getpass.getpass", side_effect=[clave_temporal, clave_temporal]), \
                redirect_stdout(salida), redirect_stderr(errores):
            self.assertEqual(configurar_administrador(["--cuentas", str(archivos.cuentas)]), 0)
        self.assertNotIn(clave_temporal, salida.getvalue() + errores.getvalue())
        ahora = [datetime(2026, 11, 10, tzinfo=timezone.utc)]
        buzon = []
        argumentos = dict(ruta_cuentas=archivos.cuentas, ruta_tarifas=archivos.tarifas,
            carpeta_historial=archivos.historiales, clave_hmac=token_bytes(32),
            enviar_pin=lambda destinatario, codigo: buzon.append(codigo), reloj=lambda: ahora[0])
        origen, url = {"Origin": "https://localhost"}, "https://localhost"
        with abrir_app_csv_experimental(**argumentos) as app:
            cliente = app.test_client()
            def post(ruta, datos):
                return cliente.post(ruta, base_url=url, headers=origen, json=datos)
            self.assertEqual(post("/acceso/administrador", {"usuario": "Admin", "contrasena": clave_temporal}).json["paso"], "pin_pendiente")
            self.assertEqual(post("/acceso/administrador/pin", {"codigo": buzon[-1]}).json["paso"], "cambio_pendiente")
            self.assertEqual(post("/acceso/administrador/cambio", {"contrasena_nueva": clave_nueva}).json["paso"], "volver_a_iniciar")
            ahora[0] += timedelta(seconds=61)
            self.assertEqual(post("/acceso/administrador", {"usuario": "Admin", "contrasena": clave_nueva}).json["paso"], "pin_pendiente")
            self.assertEqual(post("/acceso/administrador/pin", {"codigo": buzon[-1]}).json["paso"], "acceso_completado")
            self.assertEqual(post("/api/carrera/iniciar", {"revision": 0}).status_code, 200)
            self.assertEqual(post("/api/carrera/finalizar", {"revision": 1}).status_code, 200)
            self.assertEqual(len(cliente.get("/api/historial", base_url=url).json["carreras"]), 1)
        ahora[0] += timedelta(seconds=61)
        with abrir_app_csv_experimental(**argumentos) as app:
            cliente = app.test_client()
            self.assertEqual(cliente.post("/acceso/administrador", base_url=url, headers=origen,
                json={"usuario": "Admin", "contrasena": clave_nueva}).json["paso"], "pin_pendiente")
            self.assertEqual(cliente.post("/acceso/administrador/pin", base_url=url, headers=origen,
                json={"codigo": buzon[-1]}).json["paso"], "acceso_completado")
            self.assertEqual(len(cliente.get("/api/historial", base_url=url).json["carreras"]), 1)


if __name__ == "__main__":
    unittest.main()
