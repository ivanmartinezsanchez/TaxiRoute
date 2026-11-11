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
