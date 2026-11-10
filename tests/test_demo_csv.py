"""Demostración CSV: únicamente carpetas y cuentas ficticias."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
from threading import Thread
from urllib.request import HTTPSHandler, build_opener
import ssl

from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_csv import (ARCHIVO_MARCADOR, CORREO_FICTICIO, preparar_demo_csv,
                                servidor_demo_csv)
from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO


class PruebasDemoCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.carpeta = self.raiz / "demo"
        certs = self.raiz / "certificados"
        certs.mkdir()
        self.certificado, self.clave = certificado_temporal(certs)
        self.url = "https://127.0.0.1"
        self.origen = {"Origin": self.url}
        self.codigos = []

    def abrir(self):
        return servidor_demo_csv(self.carpeta, self.certificado, self.clave,
                                 puerto=0, mostrar_codigo=self.codigos.append)

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.origen, json=datos)

    def conductor(self, app):
        cliente = app.test_client()
        respuesta = self.post(cliente, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO})
        self.assertEqual(respuesta.status_code, 200)
        return cliente

    def test_inicio_aislamiento_pin_ficticio_y_sin_gmail(self):
        with patch("taxiroute.servidor_csv_local.EnviadorGmail", side_effect=AssertionError("No usar Gmail")):
            with self.abrir() as servidor:
                self.assertEqual(servidor.server_address[0], "127.0.0.1")
                admin = servidor.app.test_client()
                respuesta = self.post(admin, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO})
                self.assertEqual((respuesta.status_code, respuesta.json["paso"]), (200, "pin_pendiente"))
                self.assertEqual(len(self.codigos), 1)
                self.assertEqual(len(self.codigos[0]), 7)
                self.assertEqual(self.post(admin, "/acceso/administrador/pin", {"codigo": self.codigos[0]}).status_code, 200)
                self.assertEqual(self.conductor(servidor.app).get("/api/historial", base_url=self.url).json["carreras"], [])
        self.assertTrue((self.carpeta / ARCHIVO_MARCADOR).is_file())
        self.assertEqual(CORREO_FICTICIO, "demo@example.invalid")

    def test_reinicio_permanece_carrera_para_revisar_y_guardar(self):
        with self.abrir() as servidor:
            cliente = self.conductor(servidor.app)
            self.assertEqual(self.post(cliente, "/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())}).status_code, 200)
        with self.abrir() as servidor:
            cliente = self.conductor(servidor.app)
            self.assertEqual(cliente.get("/api/carrera", base_url=self.url).status_code, 409)
            punto = cliente.get("/api/carrera/revision-recuperacion", base_url=self.url).json["pendiente"]
            datos = {"revision": punto["revision_recuperacion"], "ultimo_control": punto["ultimo_control"],
                     "huella": punto["huella"], "confirmar": True}
            self.assertEqual(self.post(cliente, "/api/carrera/confirmar-recuperacion", datos).status_code, 200)
            historial = cliente.get("/api/historial", base_url=self.url).json["carreras"]
            self.assertEqual(len(historial), 1)
            self.assertEqual(historial[0]["fin"], punto["ultimo_control"])
            self.assertEqual(historial[0]["importe"], punto["importe_recuperado"])

    def test_carpeta_ajena_y_archivo_no_se_modifican(self):
        self.carpeta.mkdir()
        archivo = self.carpeta / "conservar.txt"
        archivo.write_text("no modificar", encoding="utf-8")
        with self.assertRaises(ValueError):
            preparar_demo_csv(self.carpeta)
        self.assertEqual(archivo.read_text(encoding="utf-8"), "no modificar")
        self.assertEqual([x.name for x in self.carpeta.iterdir()], ["conservar.txt"])

    def test_carpeta_repo_y_preparacion_parcial_se_rechazan(self):
        repo = self.raiz / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with self.assertRaises(ValueError):
            preparar_demo_csv(repo / "demo")
        self.assertFalse((repo / "demo").exists())
        with patch("taxiroute.demo_csv.crear_cuenta", side_effect=OSError("simulado")):
            with self.assertRaises(OSError):
                preparar_demo_csv(self.carpeta)
        self.assertTrue(self.carpeta.is_dir())
        self.assertFalse((self.carpeta / ARCHIVO_MARCADOR).exists())
        with self.assertRaises(ValueError):
            preparar_demo_csv(self.carpeta)

    def test_rechaza_cambio_de_correo_admin_sin_transporte_real(self):
        preparar_demo_csv(self.carpeta)
        from taxiroute.base_cuentas import conectar
        with conectar(self.carpeta / "cuentas.sqlite3") as conexion:
            conexion.execute("UPDATE configuracion_administrador SET correo_verificacion='otra@example.invalid'")
        with self.assertRaises(ValueError):
            with self.abrir():
                self.fail("No debe abrir una demo con otro buzón")
        self.assertEqual(self.codigos, [])

    def test_servidor_https_real_responde_solo_en_bucle_local(self):
        with self.abrir() as servidor:
            hilo = Thread(target=servidor.serve_forever, daemon=True)
            hilo.start()
            try:
                contexto = ssl.create_default_context(cafile=str(self.certificado))
                cliente = build_opener(HTTPSHandler(context=contexto))
                with cliente.open(f"https://127.0.0.1:{servidor.server_port}/acceso", timeout=5) as respuesta:
                    self.assertEqual(respuesta.status, 200)
                    self.assertIn(b"TaxiRoute", respuesta.read())
                self.assertEqual(servidor.server_address[0], "127.0.0.1")
            finally:
                servidor.shutdown()
                hilo.join(timeout=5)
                self.assertFalse(hilo.is_alive())

    def test_segunda_instancia_no_comparte_la_demo_activa(self):
        from taxiroute.bloqueo import HistorialOcupado
        with self.abrir():
            with self.assertRaises(HistorialOcupado):
                with self.abrir():
                    self.fail("Dos instancias no deben compartir la demostración")

    def test_marcador_enlace_se_rechaza_sin_modificar_destino(self):
        preparar_demo_csv(self.carpeta)
        marcador = self.carpeta / ARCHIVO_MARCADOR
        texto = marcador.read_text(encoding="utf-8")
        destino = self.raiz / "marcador-ajeno"
        destino.write_text(texto, encoding="utf-8")
        marcador.unlink()
        try:
            marcador.symlink_to(destino)
        except OSError:
            self.skipTest("El sistema no permite crear enlaces simbólicos para esta prueba")
        with self.assertRaises(ValueError):
            preparar_demo_csv(self.carpeta)
        self.assertEqual(destino.read_text(encoding="utf-8"), texto)
