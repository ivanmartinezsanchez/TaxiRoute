"""Primer arranque con entrada, correo y cuenta ficticios."""
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import Mock, patch

from taxiroute.iniciar import ErrorPrimerArranque, main


class TerminalFicticia(StringIO):
    def isatty(self):
        return True


class PruebasPrimerArranque(unittest.TestCase):
    def setUp(self):
        temporal=TemporaryDirectory(prefix="taxi iván ")
        self.addCleanup(temporal.cleanup)
        self.carpeta=Path(temporal.name).resolve()/"instalacion"
        self.clave="TemporalPrueba123!"

    def ejecutar(self, *, entradas=None, claves=None, interactiva=True):
        salida=TerminalFicticia(); errores=StringIO(); entrada=Mock(isatty=Mock(return_value=interactiva))
        valores=entradas if entradas is not None else ["AdminPrueba", "Administrador prueba", "demo@example.invalid", "GUARDADOS"]
        with patch("sys.stdin", entrada), patch("sys.stdout", salida), redirect_stderr(errores), \
             patch("builtins.input",side_effect=valores), patch("getpass.getpass",side_effect=claves or [self.clave,self.clave]), \
             patch("taxiroute.iniciar._comprobar_requisitos"), \
             patch("taxiroute.iniciar.arrancar",return_value="Iniciado") as iniciar:
            codigo=main(["--carpeta",str(self.carpeta)])
        return codigo,salida.getvalue(),errores.getvalue(),iniciar

    def test_crea_una_vez_y_reutiliza_sin_pedir_clave_ni_cambiar_datos(self):
        codigo,salida,error,iniciar=self.ejecutar()
        self.assertEqual((codigo,error),(0,""));self.assertNotIn(self.clave,salida);iniciar.assert_called_once()
        with closing(sqlite3.connect(self.carpeta/"datos"/"cuentas.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0],5)
        codigo,salida,error,iniciar=self.ejecutar(entradas=[])
        self.assertEqual((codigo,error),(0,""));iniciar.assert_called_once()
        self.assertNotIn("Primera ejecución",salida)

    def test_claves_distintas_no_crean_instalacion(self):
        codigo,salida,error,iniciar=self.ejecutar(claves=[self.clave,"DistintaPrueba456!"])
        self.assertEqual(codigo,1);self.assertFalse(self.carpeta.exists());iniciar.assert_not_called()
        self.assertIn("contraseñas no coinciden",error);self.assertNotIn(self.clave,salida+error)

    def test_terminal_sin_entrada_oculta_no_crea_instalacion(self):
        import getpass
        import warnings
        salida=TerminalFicticia(); errores=StringIO(); entrada=Mock(isatty=Mock(return_value=True))
        def no_oculta(*args):
            warnings.warn("sin entrada protegida", getpass.GetPassWarning)
        with patch("sys.stdin",entrada), patch("sys.stdout",salida), redirect_stderr(errores), \
             patch("taxiroute.iniciar._comprobar_requisitos"), \
             patch("builtins.input",side_effect=["AdminPrueba","Prueba","demo@example.invalid"]), \
             patch("getpass.getpass",side_effect=no_oculta), patch("taxiroute.iniciar.arrancar") as iniciar:
            self.assertEqual(main(["--carpeta",str(self.carpeta)]),1)
        self.assertFalse(self.carpeta.exists());iniciar.assert_not_called()
        self.assertIn("no puede ocultar",errores.getvalue())

    def test_no_interactiva_no_crea_instalacion(self):
        codigo,salida,error,iniciar=self.ejecutar(interactiva=False)
        self.assertEqual(codigo,1);self.assertFalse(self.carpeta.exists());iniciar.assert_not_called()

    def test_cancelacion_conserva_carpeta_para_revision(self):
        codigo,salida,error,iniciar=self.ejecutar(entradas=["AdminPrueba","Prueba","demo@example.invalid",""])
        self.assertEqual(codigo,130);self.assertTrue(self.carpeta.is_dir())
        self.assertIn("consérvalos",error);iniciar.assert_not_called()

    def test_requisitos_ausentes_no_crean_instalacion(self):
        salida=TerminalFicticia(); errores=StringIO(); entrada=Mock(isatty=Mock(return_value=True))
        with patch("sys.stdin",entrada), patch("sys.stdout",salida), redirect_stderr(errores), \
             patch("taxiroute.iniciar._comprobar_requisitos",side_effect=ErrorPrimerArranque("Docker o Gmail no disponible")), \
             patch("builtins.input") as pedir, patch("taxiroute.iniciar.arrancar") as iniciar:
            self.assertEqual(main(["--carpeta",str(self.carpeta)]),1)
        self.assertFalse(self.carpeta.exists());pedir.assert_not_called();iniciar.assert_not_called()
        self.assertIn("Docker o Gmail", errores.getvalue())

    @patch("taxiroute.iniciar._almacen_windows")
    @patch("taxiroute.iniciar._docker")
    def test_comprobacion_rechaza_correo_no_autorizado(self, docker, almacen):
        from taxiroute.iniciar import _comprobar_requisitos
        almacen.return_value.get_password.return_value = None
        with self.assertRaises(ValueError): _comprobar_requisitos()
        docker.assert_called_once()

    def test_instalacion_ajena_no_se_sobrescribe(self):
        self.carpeta.mkdir(); archivo=self.carpeta/"importante.txt";archivo.write_text("conservar",encoding="utf-8")
        codigo,salida,error,iniciar=self.ejecutar(entradas=[])
        self.assertEqual(codigo,1);self.assertEqual(archivo.read_text(encoding="utf-8"),"conservar");iniciar.assert_not_called()


if __name__=="__main__":
    unittest.main()
