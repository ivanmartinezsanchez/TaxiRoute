from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from taxiroute.preparar_certificados_red import main


class PruebasPrepararCertificados(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix='taxi-cert-')
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name).resolve()/'nueva'
        self.argv = ['--carpeta', str(self.carpeta), '--direccion', '192.168.1.25']
        for nombre in ('comprobar_direccion_local', 'verificar_permisos_windows'):
            parche = patch('taxiroute.preparar_certificados_red.'+nombre)
            parche.start()
            self.addCleanup(parche.stop)

    def ejecutar(self):
        salida, error = StringIO(), StringIO()
        with redirect_stdout(salida), redirect_stderr(error):
            codigo = main(self.argv)
        return codigo, salida.getvalue(), error.getvalue()

    def test_prepara_material_y_solo_muestra_certificado_publico(self):
        codigo, salida, error = self.ejecutar()
        self.assertEqual((codigo,error), (0,''))
        self.assertIn('autoridad.cer',salida)
        self.assertNotIn('autoridad.key',salida)
        self.assertTrue((self.carpeta/'autoridad-privada'/'autoridad.key').is_file())

    def test_no_sobrescribe_una_carpeta_existente(self):
        self.carpeta.mkdir()
        archivo = self.carpeta/'conservar'
        archivo.write_text('conservar',encoding='utf-8')
        codigo, _, _ = self.ejecutar()
        self.assertEqual(codigo,1)
        self.assertEqual(archivo.read_text(encoding='utf-8'),'conservar')
