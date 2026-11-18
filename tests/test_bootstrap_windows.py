"""Pruebas del arranque en carpetas temporales, sin red ni credenciales."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
import os
import shutil
import subprocess
import sys


@skipUnless(os.name == "nt" and sys.version_info[:2] == (3, 13), "Se requiere Windows con Python 3.13 para probar el CMD")
class PruebasBootstrapWindows(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="taxi-bootstrap-")
        self.addCleanup(temp.cleanup)
        self.raiz = Path(temp.name).resolve()
        fuente = Path(__file__).resolve().parents[1] / "iniciar-taxiroute-proxy-experimental.cmd"
        self.comando = self.raiz / fuente.name
        shutil.copyfile(fuente, self.comando)
        (self.raiz / "requirements.txt").write_text("", encoding="utf-8")
        paquete = self.raiz / "taxiroute"
        paquete.mkdir()
        (paquete / "__init__.py").write_text("", encoding="utf-8")
        (paquete / "iniciar_proxy_local_experimental.py").write_text(
            'import sys\nprint("LANZADOR_FICTICIO", *sys.argv[1:])\n', encoding="utf-8")
        lanzador = self.raiz / "lanzador"
        lanzador.mkdir()
        # Utiliza el Python 3.13 del entorno de pruebas, sin depender de que
        # GitHub Actions lo haya registrado en el lanzador global de Windows.
        (lanzador / "py.cmd").write_text(
            '@echo off\r\nif not "%~1"=="-3.13" exit /b 1\r\n>> "%~dp0usado.txt" echo lanzador\r\n"' + sys.executable + '" %2 %3 %4 %5 %6 %7 %8 %9\r\n',
            encoding="utf-8")
        self.entorno = {k: v for k, v in os.environ.items() if not k.upper().startswith("PIP_")}
        self.entorno.pop("PYTHONHOME", None)
        self.entorno.update(PYTHONPATH=str(self.raiz), PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
                            PIP_CONFIG_FILE=os.devnull, PYTHON_MANAGER_AUTOMATIC_INSTALL="0",
                            PATH=str(lanzador) + os.pathsep + os.environ.get("PATH", ""))

    def ejecutar(self, *args, entorno=None):
        cmd = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
        return subprocess.run([cmd, "/d", "/c", str(self.comando), *args], cwd=self.raiz,
                              input=b"\r\n", capture_output=True, env=entorno or self.entorno, timeout=120)

    def test_crea_entorno_y_luego_lo_reutiliza(self):
        primera = self.ejecutar("--conectar-gmail")
        self.assertEqual(primera.returncode, 0, primera.stderr.decode(errors="replace"))
        self.assertIn(b"LANZADOR_FICTICIO --conectar-gmail", primera.stdout)
        self.assertTrue((self.raiz / ".venv" / "Scripts" / "python.exe").is_file())
        self.assertTrue((self.raiz / "lanzador" / "usado.txt").is_file())
        self.assertFalse((self.raiz / ".venv" / ".taxiroute-preparacion-incompleta").exists())
        marca = (self.raiz / ".venv" / "pyvenv.cfg").stat().st_mtime_ns
        segunda = self.ejecutar("--no-abrir")
        self.assertEqual(segunda.returncode, 0, segunda.stderr.decode(errors="replace"))
        self.assertIn(b"LANZADOR_FICTICIO --no-abrir", segunda.stdout)
        self.assertNotIn(b"--conectar-gmail", segunda.stdout)
        self.assertNotIn(b"Preparando el entorno", segunda.stdout)
        self.assertEqual((self.raiz / ".venv" / "pyvenv.cfg").stat().st_mtime_ns, marca)

    def test_entorno_preexistente_incompleto_no_se_modifica(self):
        carpeta = self.raiz / ".venv"
        carpeta.mkdir()
        (carpeta / "conservar.txt").write_text("conservar", encoding="utf-8")
        resultado = self.ejecutar()
        self.assertEqual(resultado.returncode, 1)
        self.assertEqual((carpeta / "conservar.txt").read_text(encoding="utf-8"), "conservar")
        self.assertFalse((carpeta / ".taxiroute-preparacion-incompleta").exists())
        self.assertNotIn(b"LANZADOR_FICTICIO", resultado.stdout)

    def test_marcador_pendiente_impide_usar_python_existente(self):
        carpeta = self.raiz / ".venv"
        (carpeta / "Scripts").mkdir(parents=True)
        (carpeta / "Scripts" / "python.exe").write_text("ficticio", encoding="utf-8")
        (carpeta / ".taxiroute-preparacion-incompleta").write_text("pendiente", encoding="utf-8")
        resultado = self.ejecutar()
        self.assertEqual(resultado.returncode, 1)
        self.assertTrue((carpeta / ".taxiroute-preparacion-incompleta").is_file())
        self.assertNotIn(b"LANZADOR_FICTICIO", resultado.stdout)

    def test_fallo_de_dependencias_conserva_marcador_y_no_inicia(self):
        (self.raiz / "requirements.txt").write_text("taxiroute-paquete-inexistente==0.0.0\n", encoding="utf-8")
        primera = self.ejecutar()
        self.assertEqual(primera.returncode, 1)
        self.assertTrue((self.raiz / ".venv" / ".taxiroute-preparacion-incompleta").is_file())
        self.assertNotIn(b"LANZADOR_FICTICIO", primera.stdout)
        segunda = self.ejecutar()
        self.assertEqual(segunda.returncode, 1)
        self.assertNotIn(b"LANZADOR_FICTICIO", segunda.stdout)

    def test_sin_python_compatible_no_crea_entorno(self):
        entorno = dict(self.entorno)
        entorno["PATH"] = ""
        resultado = self.ejecutar(entorno=entorno)
        self.assertEqual(resultado.returncode, 1)
        self.assertFalse((self.raiz / ".venv").exists())
