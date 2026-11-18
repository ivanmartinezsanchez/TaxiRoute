"""Integración del asistente con transición y bases ficticias; Docker simulado."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import patch

import test_transicion_local_red as fixtures
from taxiroute.asistente_transicion_red import main
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto_red
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION


class PruebasAsistenteIntegrado(TestCase):
    setUp = fixtures.PruebasTransicionLocalRed.setUp
    configurar = fixtures.PruebasTransicionLocalRed.configurar

    def test_transicion_comprobada_con_servicios_simulados(self):
        self.configurar()
        opciones = self.opciones
        argv = ['--origen', str(opciones['origen']), '--copia', str(opciones['copia']),
                '--destino', str(opciones['destino']), '--direccion', opciones['direccion'],
                '--puerto', str(opciones['puerto']), '--proyecto', opciones['proyecto']]
        salida, errores = StringIO(), StringIO()
        with patch('taxiroute.asistente_transicion_red.verificar_permisos_windows'), \
             patch('taxiroute.asistente_transicion_red.comprobar_direccion_local'), \
             patch('taxiroute.verificar_copia_previa.verificar_permisos_windows'), \
             patch('taxiroute.asistente_transicion_red.iniciar_red', return_value=0) as iniciar, \
             redirect_stdout(salida), redirect_stderr(errores):
            codigo = main(argv)
        self.assertEqual((codigo, errores.getvalue()), (0, ''))
        self.assertTrue((self.identidad / MARCADOR_ACTUALIZACION).is_file())
        identidad = opciones['destino'] / 'proxy-local'
        self.assertEqual(leer_manifiesto_red(identidad).direccion, opciones['direccion'])
        iniciar.assert_called_once_with(['--identidad', str(identidad)])
