"""Alta local ficticia: los códigos mostrados en estas pruebas no son reales."""
from contextlib import redirect_stderr, redirect_stdout
import builtins
import getpass
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import warnings

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.codigos_recuperacion_experimentales import consumir_codigo_en_transaccion
from taxiroute.configurar_admin_local import main
from taxiroute.cuentas import crear_cuenta


class TerminalFicticia(StringIO):
    def isatty(self):
        return True


class PruebasAltaLocal(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdminPrueba123!"

    def ejecutar(self, confirmacion, *, claves=None, interactiva=True, salida_interactiva=True):
        salida, errores = TerminalFicticia(), StringIO()
        if not salida_interactiva:
            salida = StringIO()
        entrada = Mock(isatty=Mock(return_value=interactiva))
        valores = ["AdminPrueba", "Admin de prueba", "prueba@example.invalid", confirmacion]
        with patch("sys.stdin", entrada), patch("builtins.input", side_effect=valores), \
                patch("getpass.getpass", side_effect=claves or [self.clave, self.clave]), \
                redirect_stdout(salida), redirect_stderr(errores):
            resultado = main(["--cuentas", str(self.ruta)])
        return resultado, salida.getvalue(), errores.getvalue()

    def test_cuenta_se_crea_solo_despues_de_confirmar_y_codigos_funcionan(self):
        resultado, salida, error = self.ejecutar("GUARDADOS")
        self.assertEqual((resultado, error), (0, ""))
        self.assertNotIn(self.clave, salida)
        self.assertIn("Cuenta creada", salida)
        codigos = [linea for linea in salida.splitlines() if len(linea) == 43]
        self.assertEqual(len(codigos), 5)
        with conectar(self.ruta) as conexion:
            usuario = conexion.execute("SELECT id, cambio_obligatorio FROM usuarios").fetchone()
            self.assertEqual(usuario[1], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 5)
            conexion.execute("BEGIN IMMEDIATE")
            self.assertTrue(consumir_codigo_en_transaccion(conexion, usuario[0], codigos[0]))
            conexion.rollback()

    def test_cancelar_despues_de_ver_codigos_no_crea_cuenta(self):
        resultado, salida, errores = self.ejecutar("")
        self.assertEqual(resultado, 130)
        self.assertIn("aún no", salida.lower())
        self.assertIn("no son válidos", errores)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)

    def test_rechaza_salida_redirigida_o_entrada_no_interactiva(self):
        for entrada, salida in ((False, True), (True, False)):
            with self.subTest(entrada=entrada, salida=salida):
                resultado, _, error = self.ejecutar("GUARDADOS", interactiva=entrada, salida_interactiva=salida)
                self.assertEqual(resultado, 2)
                self.assertIn("terminal interactiva", error)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)

    def test_contrasenas_distintas_no_muestran_codigos_ni_crean_cuenta(self):
        resultado, salida, errores = self.ejecutar("GUARDADOS", claves=[self.clave, "Diferente456!"])
        self.assertEqual(resultado, 1)
        self.assertIn("no coinciden", errores)
        self.assertNotIn("Guarda estos códigos", salida)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)

    def test_cuenta_existente_no_muestra_codigos_ni_se_altera(self):
        crear_cuenta(self.ruta, nombre_usuario="Otro", nombre_visible="Prueba", contrasena="ConductorPrueba123!")
        resultado, salida, errores = self.ejecutar("GUARDADOS")
        self.assertEqual(resultado, 1)
        self.assertNotIn("Guarda estos códigos", salida)
        self.assertIn("No se ha creado la cuenta", errores)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT nombre_usuario FROM usuarios").fetchone()[0], "Otro")
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)

    def test_terminal_que_no_oculta_contrasena_no_crea_cuenta(self):
        salida, errores = TerminalFicticia(), StringIO()
        def no_oculta(*argumentos):
            warnings.warn("sin eco seguro", getpass.GetPassWarning)
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), \
                patch("builtins.input", side_effect=["AdminPrueba", "Prueba", "prueba@example.invalid"]), \
                patch("getpass.getpass", side_effect=no_oculta), redirect_stdout(salida), redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 2)
        self.assertIn("No se puede ocultar", errores.getvalue())
        self.assertNotIn("Guarda estos códigos", salida.getvalue())
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)

    def test_error_incierto_pide_conservar_codigos_y_revisar(self):
        salida, errores = TerminalFicticia(), StringIO()
        def fallo_incierto(*argumentos, **opciones):
            opciones["confirmar_codigos"](("a" * 43,) * 5)
            raise OSError("detalle privado")
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), \
                patch("builtins.input", side_effect=["AdminPrueba", "Prueba", "prueba@example.invalid", "GUARDADOS"]), \
                patch("getpass.getpass", side_effect=[self.clave, self.clave]), \
                patch("taxiroute.configurar_admin_local.preparar_primer_administrador_con_codigos", side_effect=fallo_incierto), \
                redirect_stdout(salida), redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 1)
        self.assertIn("Conserva los códigos", errores.getvalue())
        self.assertIn("revisa la base", errores.getvalue())
        self.assertNotIn("detalle privado", errores.getvalue())

    def test_si_falla_mensaje_de_exito_pide_revisar_y_conservar_codigos(self):
        salida, errores = TerminalFicticia(), StringIO()
        imprimir = builtins.print
        def fallo_mensaje(*argumentos, **opciones):
            if argumentos and str(argumentos[0]).startswith("Cuenta creada."):
                raise OSError("salida interrumpida")
            return imprimir(*argumentos, **opciones)
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), \
                patch("builtins.input", side_effect=["AdminPrueba", "Prueba", "prueba@example.invalid", "GUARDADOS"]), \
                patch("getpass.getpass", side_effect=[self.clave, self.clave]), \
                patch("builtins.print", side_effect=fallo_mensaje), redirect_stdout(salida), redirect_stderr(errores):
            self.assertEqual(main(["--cuentas", str(self.ruta)]), 1)
        self.assertIn("Conserva los códigos", errores.getvalue())
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 5)

    def test_si_otra_cuenta_aparece_antes_de_confirmar_no_se_sobrescribe(self):
        salida, errores = TerminalFicticia(), StringIO()
        valores = iter(["AdminPrueba", "Admin de prueba", "prueba@example.invalid"])
        def entrada(pregunta):
            try:
                return next(valores)
            except StopIteration:
                crear_cuenta(self.ruta, nombre_usuario="Otro", nombre_visible="Prueba", contrasena="ConductorPrueba123!")
                return "GUARDADOS"
        with patch("sys.stdin", Mock(isatty=Mock(return_value=True))), patch("builtins.input", side_effect=entrada), \
                patch("getpass.getpass", side_effect=[self.clave, self.clave]), \
                redirect_stdout(salida), redirect_stderr(errores):
            resultado = main(["--cuentas", str(self.ruta)])
        self.assertEqual(resultado, 1)
        self.assertIn("no están activos", errores.getvalue())
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT nombre_usuario FROM usuarios").fetchone()[0], "Otro")
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
