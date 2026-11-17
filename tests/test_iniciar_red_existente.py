from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, CargaProxyPosibleExposicion
from taxiroute.iniciar_red_existente import main
from taxiroute.reutilizacion_red import ErrorReutilizacionRed


class PruebasInicioRedExistente(unittest.TestCase):
    def setUp(self):
        self.evidencia = SimpleNamespace(backend=SimpleNamespace(identificador="a" * 64))
        self.identidad = Path("identidad-ficticia")
        self.argumentos = ["--identidad", str(self.identidad)]
        self.proveedor = Mock(return_value="secreto-ficticio")
        prefijo = "taxiroute.iniciar_red_existente."
        self.reutilizar = patch(prefijo + "reutilizar_red", return_value=self.evidencia).start()
        self.leer = patch(prefijo + "leer_manifiesto_red", return_value=SimpleNamespace(direccion="192.168.1.3", puerto=18775)).start()
        self.verificar = patch(prefijo + "verificar_instalacion_red", return_value=self.evidencia).start()
        self.estado = patch(prefijo + "_estado_archivo", return_value="ausente").start()
        self.cargar = patch(prefijo + "cargar_correo_red").start()
        self.almacen = patch(prefijo + "_proveedor_windows").start()
        self.addCleanup(patch.stopall)

    def ejecutar(self, *extra):
        salida, error = StringIO(), StringIO()
        with redirect_stdout(salida), redirect_stderr(error):
            codigo = main(self.argumentos + list(extra), proveedor=self.proveedor)
        return codigo, salida.getvalue(), error.getvalue()

    def test_sin_opcion_no_consulta_ni_carga_correo(self):
        codigo, salida, _ = self.ejecutar()
        self.assertEqual(codigo, 0)
        self.assertIn("https://192.168.1.3:18775/", salida)
        self.proveedor.assert_not_called()
        self.almacen.assert_not_called()
        self.estado.assert_not_called()
        self.cargar.assert_not_called()

    def test_opcion_carga_con_proveedor_diferido(self):
        codigo, salida, _ = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 0)
        self.assertIn("Perfil de Gmail cargado", salida)
        self.cargar.assert_called_once_with(identidad=self.identidad, evidencia_esperada=self.evidencia, proveedor=self.proveedor)
        self.proveedor.assert_not_called()

    def test_opcion_usa_almacen_solo_dentro_de_la_carga(self):
        self.almacen.return_value = "perfil-ficticio"
        self.cargar.side_effect = lambda **opciones: opciones["proveedor"]()
        salida, error = StringIO(), StringIO()
        with redirect_stdout(salida), redirect_stderr(error):
            codigo = main(self.argumentos + ["--conectar-gmail"])
        self.assertEqual(codigo, 0)
        self.almacen.assert_called_once_with(self.identidad)
        self.assertNotIn("perfil-ficticio", salida.getvalue() + error.getvalue())

    def test_sin_opcion_ignora_proveedor_dado(self):
        self.cargar.side_effect = lambda **opciones: opciones["proveedor"]()
        codigo, _, _ = self.ejecutar()
        self.assertEqual(codigo, 0)
        self.proveedor.assert_not_called()
        self.almacen.assert_not_called()

    def test_archivo_preexistente_no_consulta_proveedor(self):
        self.estado.return_value = "presente"
        codigo, salida, _ = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 0)
        self.assertIn("no se ha comprobado su identidad o vigencia", salida)
        self.proveedor.assert_not_called()
        self.cargar.assert_not_called()

    def test_reutilizacion_o_evidencia_invalidas_no_cargan(self):
        for error in (ErrorReutilizacionRed("detalle-privado"),):
            self.reutilizar.side_effect = error
            codigo, salida, mensaje = self.ejecutar("--conectar-gmail")
            self.assertEqual(codigo, 1)
            self.assertNotIn("detalle-privado", salida + mensaje)
            self.cargar.assert_not_called()
            self.proveedor.assert_not_called()
        self.reutilizar.side_effect = None
        self.verificar.return_value = None
        self.assertEqual(self.ejecutar("--conectar-gmail")[0], 1)
        self.cargar.assert_not_called()

    def test_exposicion_posible_se_distingue_y_no_revela_secreto(self):
        self.cargar.side_effect = CargaProxyPosibleExposicion("secreto-ficticio")
        codigo, salida, error = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 1)
        self.assertIn("pudo haberse transmitido", error)
        self.assertNotIn("secreto-ficticio", salida + error)

    def test_carga_rechazada_no_anuncia_exito(self):
        self.cargar.side_effect = CargaProxyAntesEnvio("fallo")
        codigo, salida, error = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 1)
        self.assertNotIn("Perfil de Gmail cargado", salida)
        self.assertIn("No se pudo verificar", error)

    def test_manifiesto_cambiado_no_publica_url(self):
        original = SimpleNamespace(direccion="192.168.1.3", puerto=18775)
        cambiado = SimpleNamespace(direccion="192.168.1.4", puerto=18775)
        self.leer.side_effect = [original, cambiado]
        codigo, salida, _ = self.ejecutar()
        self.assertEqual(codigo, 1)
        self.assertNotIn("Acceso de red", salida)
        self.cargar.assert_not_called()

    def test_identidad_cambia_tras_carga_no_publica_url_ni_exito(self):
        self.verificar.side_effect = [self.evidencia, None]
        codigo, salida, error = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 1)
        self.assertNotIn("Acceso de red", salida)
        self.assertNotIn("Perfil de Gmail cargado", salida)
        self.assertIn("Se cargó el correo", error)
        self.cargar.assert_called_once()

    def test_estado_desconocido_no_accede_a_correo(self):
        self.estado.return_value = "desconocido"
        codigo, _, _ = self.ejecutar("--conectar-gmail")
        self.assertEqual(codigo, 1)
        self.proveedor.assert_not_called()
        self.cargar.assert_not_called()
