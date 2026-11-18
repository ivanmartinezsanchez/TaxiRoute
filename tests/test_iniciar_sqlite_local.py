from contextlib import contextmanager
from io import StringIO
from unittest import TestCase
from unittest.mock import patch

from taxiroute.iniciar_sqlite_local import main


class PruebasLanzadorSQLite(TestCase):
    def setUp(self):
        self.args = ['--cuentas', 'cuentas.db', '--historial', 'historial.db', '--tarifas', 'tarifas.json',
                     '--certificado', 'cert.pem', '--clave', 'clave.pem', '--puerto', '8769']

    def test_arranca_y_se_detiene_sin_solicitar_correo(self):
        capturado = {}
        class Servidor:
            def serve_forever(self):
                raise KeyboardInterrupt
        @contextmanager
        def servidor(**opciones):
            capturado.update(opciones)
            yield Servidor()
        with patch('taxiroute.iniciar_sqlite_local.servidor_sqlite_experimental', servidor), patch('sys.stdout', new_callable=StringIO) as salida:
            self.assertEqual(main(self.args), 0)
        self.assertEqual(capturado['puerto'], 8769)
        self.assertEqual(capturado['direccion'], '127.0.0.1')
        self.assertTrue(callable(capturado['enviar_pin']))
        self.assertIn('127.0.0.1:8769', salida.getvalue())
        self.assertIn('Servidor detenido', salida.getvalue())

    def test_modo_red_muestra_aviso_y_transmite_direccion(self):
        capturado = {}
        class Servidor:
            def serve_forever(self):
                raise KeyboardInterrupt
        @contextmanager
        def servidor(**opciones):
            capturado.update(opciones)
            yield Servidor()
        with patch('taxiroute.iniciar_sqlite_local.servidor_sqlite_experimental', servidor), patch('sys.stdout', new_callable=StringIO) as salida:
            self.assertEqual(main(self.args + ['--direccion', '192.168.12.34']), 0)
        self.assertEqual(capturado['direccion'], '192.168.12.34')
        self.assertIn('Acceso de red experimental', salida.getvalue())

    def test_opciones_contenedor_y_secreto_se_inyectan_sin_lectura_al_arrancar(self):
        capturado = {}
        class Servidor:
            def serve_forever(self):
                raise KeyboardInterrupt
        @contextmanager
        def servidor(**opciones):
            capturado.update(opciones)
            yield Servidor()
        with patch('taxiroute.iniciar_sqlite_local.servidor_sqlite_experimental', servidor), \
             patch('taxiroute.oauth_gmail_contenedor.cliente_gmail_desde_secreto') as cliente, \
             patch('sys.stdout', new_callable=StringIO):
            resultado = main(self.args + ['--direccion', '192.168.12.34', '--escucha-contenedor',
                                         '--secreto-gmail', 'secreto-ficticio.json'])
            self.assertEqual(resultado, 0)
            self.assertTrue(capturado['escucha_contenedor'])
            cliente.assert_not_called()
            with patch('taxiroute.correo_gmail.EnviadorGmail') as enviador:
                capturado['enviar_pin']('demo@example.invalid', '1234567')
                cliente.assert_called_once()
                enviador.return_value.assert_called_once_with('demo@example.invalid', '1234567')

    def test_fallo_no_muestra_detalle_sensible(self):
        @contextmanager
        def servidor(**_):
            raise OSError('detalle privado de prueba')
            yield
        with patch('taxiroute.iniciar_sqlite_local.servidor_sqlite_experimental', servidor), patch('sys.stderr', new_callable=StringIO) as salida:
            self.assertEqual(main(self.args), 1)
        self.assertNotIn('detalle privado', salida.getvalue())


if __name__ == '__main__':
    import unittest
    unittest.main()
