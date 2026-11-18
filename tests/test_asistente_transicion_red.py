"""Pruebas aisladas del asistente; no inicia Docker ni consulta credenciales."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from taxiroute.asistente_transicion_red import main


class PruebasAsistenteTransicion(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix='taxi-asistente-')
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name).resolve()
        self.origen, self.copia, self.destino = (self.raiz / x for x in ('origen', 'copia', 'destino'))
        self.origen.mkdir()
        self.copia.mkdir()
        self.argv = ['--origen', str(self.origen), '--copia', str(self.copia),
                     '--destino', str(self.destino), '--direccion', '192.168.1.25',
                     '--puerto', '18773', '--proyecto', 'taxi-red']
        self.parches = {}
        for nombre in ('comprobar_direccion_local', 'verificar_permisos_windows',
                       'verificar_copia_previa', 'transicionar_local_a_red', 'iniciar_red'):
            parche = patch('taxiroute.asistente_transicion_red.' + nombre)
            self.parches[nombre] = parche.start()
            self.addCleanup(parche.stop)
        self.parches['transicionar_local_a_red'].return_value = SimpleNamespace(identidad=self.destino / 'proxy-local')
        self.parches['iniciar_red'].return_value = 0

    def ejecutar(self, argv=None):
        salida, errores = StringIO(), StringIO()
        with redirect_stdout(salida), redirect_stderr(errores):
            resultado = main(self.argv if argv is None else argv)
        return resultado, salida.getvalue(), errores.getvalue()

    def test_transicion_y_reinicio_sin_consultar_gmail(self):
        codigo, salida, errores = self.ejecutar()
        self.assertEqual((codigo, errores), (0, ''))
        self.parches['verificar_copia_previa'].assert_called_once_with(self.origen, self.copia)
        self.parches['transicionar_local_a_red'].assert_called_once()
        self.parches['iniciar_red'].assert_called_once_with(['--identidad', str(self.destino / 'proxy-local')])
        self.assertIn('autoridad.cer', salida)

    def test_certificados_preparados_se_validan_y_se_entregan_a_transicion(self):
        preparada = self.raiz/'certificados'
        preparada.mkdir()
        with patch('taxiroute.asistente_transicion_red.verificar_certificados_preparados') as validar:
            codigo, _, error = self.ejecutar(self.argv + ['--certificados-lan', str(preparada)])
        self.assertEqual((codigo,error),(0,''))
        validar.assert_called_once()
        self.assertEqual(self.parches['transicionar_local_a_red'].call_args.kwargs['certificados_preparados'], preparada)

    def test_certificados_invalidos_no_inician_transicion(self):
        preparada = self.raiz/'certificados'
        preparada.mkdir()
        with patch('taxiroute.asistente_transicion_red.verificar_certificados_preparados', side_effect=ValueError('inválido')):
            codigo, _, _ = self.ejecutar(self.argv + ['--certificados-lan', str(preparada)])
        self.assertEqual(codigo, 1)
        self.parches['transicionar_local_a_red'].assert_not_called()

    def test_correo_se_solicita_solo_con_flag_explicito(self):
        codigo, _, _ = self.ejecutar(self.argv + ['--conectar-gmail'])
        self.assertEqual(codigo, 0)
        self.parches['iniciar_red'].assert_called_once_with(
            ['--identidad', str(self.destino / 'proxy-local'), '--conectar-gmail'])

    def test_destino_existente_no_inicia_ni_sobrescribe(self):
        self.destino.mkdir()
        (self.destino / 'existente').write_text('conservar', encoding='utf-8')
        codigo, _, _ = self.ejecutar()
        self.assertEqual(codigo, 1)
        self.assertEqual((self.destino / 'existente').read_text(encoding='utf-8'), 'conservar')
        self.parches['transicionar_local_a_red'].assert_not_called()
        self.parches['iniciar_red'].assert_not_called()

    def test_copia_diferente_o_ip_no_asignada_no_inician(self):
        for nombre in ('verificar_copia_previa', 'comprobar_direccion_local'):
            with self.subTest(nombre=nombre):
                self.parches[nombre].side_effect = ValueError('dato privado')
                codigo, salida, errores = self.ejecutar()
                self.assertEqual(codigo, 1)
                self.assertNotIn('dato privado', salida + errores)
                self.parches['transicionar_local_a_red'].assert_not_called()
                self.parches['iniciar_red'].assert_not_called()
                self.parches[nombre].side_effect = None

    def test_parametros_invalidos_no_inician_transicion(self):
        for bandera, valor in (('--puerto', '0'), ('--puerto', '65536'), ('--proyecto', 'NO VÁLIDO')):
            with self.subTest(bandera=bandera, valor=valor):
                argv = self.argv[:]
                argv[argv.index(bandera) + 1] = valor
                codigo, _, _ = self.ejecutar(argv)
                self.assertEqual(codigo, 1)
                self.parches['transicionar_local_a_red'].assert_not_called()
                self.parches['iniciar_red'].assert_not_called()

    def test_destino_dentro_origen_no_inicia(self):
        argv = self.argv[:]
        argv[argv.index('--destino') + 1] = str(self.origen / 'nueva')
        codigo, _, _ = self.ejecutar(argv)
        self.assertEqual(codigo, 1)
        self.parches['transicionar_local_a_red'].assert_not_called()
        self.parches['iniciar_red'].assert_not_called()

    def test_fallo_durante_transicion_no_reinicia(self):
        self.parches['transicionar_local_a_red'].side_effect = RuntimeError('dato privado')
        codigo, salida, errores = self.ejecutar()
        self.assertEqual(codigo, 1)
        self.assertNotIn('dato privado', salida + errores)
        self.parches['iniciar_red'].assert_not_called()
        self.assertIn('Conserva', errores)

    def test_fallo_tras_transicion_avisa_servicio_puede_estar_activo(self):
        self.parches['iniciar_red'].return_value = 1
        codigo, salida, errores = self.ejecutar(self.argv + ['--conectar-gmail'])
        self.assertEqual(codigo, 1)
        self.assertIn('puede estar activa', errores)
        self.assertIn('barrera', salida)
