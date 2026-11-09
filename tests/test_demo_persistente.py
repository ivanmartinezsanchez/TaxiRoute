"""Pruebas de demo persistente con directorios y cuentas ficticias."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from taxiroute.bloqueo import HistorialOcupado
from taxiroute.demo_integrada import CLAVE_CONDUCTOR_DEMO, demo_persistente


class PruebasDemoPersistente(unittest.TestCase):
    def test_reinicio_detecta_carrera_y_conserva_archivos(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "demo"
            with demo_persistente(carpeta, mostrar_codigo=lambda _: None) as app:
                cliente = app.test_client()
                base, origen = "https://localhost", {"Origin": "https://localhost"}
                login = cliente.post("/acceso/conductor", base_url=base, headers=origen,
                    json={"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO})
                self.assertEqual(login.status_code, 200)
                inicio = cliente.post("/api/carrera/iniciar", base_url=base, headers=origen,
                    json={"revision": 0, "id_operacion": str(uuid4())})
                self.assertEqual(inicio.status_code, 200)
                self.assertTrue(inicio.json["activa"])
            with demo_persistente(carpeta, mostrar_codigo=lambda _: None) as app:
                cliente = app.test_client()
                self.assertEqual(cliente.post("/acceso/conductor", base_url=base, headers=origen,
                    json={"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO}).status_code, 200)
                self.assertEqual(cliente.get("/api/carrera", base_url=base).status_code, 409)
                pendiente = cliente.get("/api/recuperacion/pendiente", base_url=base)
                self.assertEqual(pendiente.status_code, 200)
                self.assertEqual(pendiente.json["pendiente"]["estado"], "activa")

    def test_directorio_ajeno_o_en_git_no_se_modifica(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "ajeno"
            carpeta.mkdir()
            original = carpeta / "archivo.txt"
            original.write_text("conservar", encoding="utf-8")
            with self.assertRaises(ValueError):
                with demo_persistente(carpeta, mostrar_codigo=lambda _: None):
                    pass
            self.assertEqual(original.read_text(encoding="utf-8"), "conservar")
            self.assertEqual([p.name for p in carpeta.iterdir()], ["archivo.txt"])
            repo = Path(temporal) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            with self.assertRaises(ValueError):
                with demo_persistente(repo / "demo", mostrar_codigo=lambda _: None):
                    pass
            self.assertFalse((repo / "demo").exists())

    def test_exclusion_de_otro_proceso(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal) / "demo"
            codigo = (
                "import sys; from taxiroute.demo_integrada import demo_persistente; "
                "ctx=demo_persistente(sys.argv[1], mostrar_codigo=lambda _:None); "
                "ctx.__enter__(); print('LISTO', flush=True); sys.stdin.readline(); ctx.__exit__(None,None,None)"
            )
            proceso = subprocess.Popen([sys.executable, "-u", "-c", codigo, str(carpeta)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proceso.stdout.readline().strip(), "LISTO")
                with self.assertRaises(HistorialOcupado):
                    with demo_persistente(carpeta, mostrar_codigo=lambda _: None):
                        pass
            finally:
                if proceso.poll() is None:
                    proceso.stdin.write("\n")
                    proceso.stdin.flush()
                proceso.communicate(timeout=10)
            self.assertEqual(proceso.returncode, 0)
            with demo_persistente(carpeta, mostrar_codigo=lambda _: None):
                pass


if __name__ == "__main__":
    unittest.main()
