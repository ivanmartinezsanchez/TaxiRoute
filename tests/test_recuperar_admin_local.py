"""CLI local con cuentas temporales y entradas simuladas, sin secretos reales."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import getpass
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import warnings

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.inicializacion_administrador import preparar_primer_administrador_con_codigos
from taxiroute.recuperar_admin_local import main


class PruebasRecuperacionLocal(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdminPrueba123!"
        self.preparada = preparar_primer_administrador_con_codigos(self.ruta, nombre_usuario="AdminPrueba",
            nombre_visible="Admin de prueba", contrasena=self.clave, correo="prueba@example.invalid")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET intentos_fallidos=15, bloqueado_en=?, proximo_intento_en=NULL WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), self.preparada.cuenta.id))

    def ejecutar(self, clave, codigo):
        salida, errores = StringIO(), StringIO()
        entrada = Mock(isatty=Mock(return_value=True))
        with patch("sys.stdin", entrada), patch("builtins.input", return_value="AdminPrueba"), \
                patch("getpass.getpass", side_effect=[clave, codigo]), \
                redirect_stdout(salida), redirect_stderr(errores):
            resultado = main(["--cuentas", str(self.ruta)])
        self.assertNotIn(clave, salida.getvalue() + errores.getvalue())
        self.assertNotIn(codigo, salida.getvalue() + errores.getvalue())
        return resultado, salida.getvalue(), errores.getvalue()

    def test_recupera_cuenta_sin_mostrar_secretos(self):
        resultado, salida, _ = self.ejecutar(self.clave, self.preparada.codigos[0])
        self.assertEqual(resultado, 0)
        self.assertIn("Cuenta desbloqueada", salida)
        with conectar(self.ruta) as conexion:
            self.assertIsNone(conexion.execute("SELECT bloqueado_en FROM usuarios").fetchone()[0])
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion WHERE usado_en IS NOT NULL").fetchone()[0], 1)

    def test_credenciales_incorrectas_no_desbloquean(self):
        resultado, _, error = self.ejecutar("contraseña incorrecta", self.preparada.codigos[0])
        self.assertEqual(resultado, 1)
        self.assertIn("No se ha podido", error)
        with conectar(self.ruta) as conexion:
            self.assertIsNotNone(conexion.execute("SELECT bloqueado_en FROM usuarios").fetchone()[0])
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion WHERE usado_en IS NOT NULL").fetchone()[0], 0)

    def test_rechaza_entrada_no_interactiva_antes_de_pedir_secretos(self):
        errores = StringIO()
        with patch("sys.stdin", Mock(isatty=Mock(return_value=False))), patch("getpass.getpass") as secreto, \
                redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 2)
        secreto.assert_not_called()
        self.assertIn("terminal interactiva", errores.getvalue())

    def test_no_lee_secretos_si_la_terminal_no_puede_ocultarlos(self):
        errores = StringIO()
        def fallo_ocultacion(*argumentos):
            warnings.warn("no se puede ocultar", getpass.GetPassWarning)
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), patch("builtins.input", return_value="AdminPrueba"), \
                patch("getpass.getpass", side_effect=fallo_ocultacion), \
                patch("taxiroute.recuperar_admin_local.recuperar_administrador") as recuperar, redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 2)
        recuperar.assert_not_called()
        self.assertIn("No se pueden ocultar", errores.getvalue())

    def test_error_de_argumentos_no_refleja_un_secreto(self):
        errores = StringIO()
        with redirect_stderr(errores), self.assertRaises(SystemExit) as salida:
            main(["--cuentas", str(self.ruta), "secreto-no-mostrar"])
        self.assertEqual(salida.exception.code, 2)
        self.assertNotIn("secreto-no-mostrar", errores.getvalue())

    def test_cancelar_no_intenta_recuperar(self):
        errores = StringIO()
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), patch("builtins.input", return_value="AdminPrueba"), \
                patch("getpass.getpass", side_effect=KeyboardInterrupt), \
                patch("taxiroute.recuperar_admin_local.recuperar_administrador") as recuperar, redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 130)
        recuperar.assert_not_called()
        self.assertIn("cancelada", errores.getvalue())

    def test_fallo_de_almacenamiento_no_muestra_detalles_ni_secretos(self):
        salida, errores = StringIO(), StringIO()
        codigo = self.preparada.codigos[0]
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), patch("builtins.input", return_value="AdminPrueba"), \
                patch("getpass.getpass", side_effect=[self.clave, codigo]), \
                patch("taxiroute.recuperar_admin_local.recuperar_administrador", side_effect=OSError("detalle privado")), \
                redirect_stdout(salida), redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 1)
        texto = salida.getvalue() + errores.getvalue()
        self.assertNotIn("detalle privado", texto)
        self.assertNotIn(self.clave, texto)
        self.assertNotIn(codigo, texto)


if __name__ == "__main__":
    unittest.main()
