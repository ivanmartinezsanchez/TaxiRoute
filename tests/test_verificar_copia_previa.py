"""Pruebas aisladas con instalaciones, usuarios y archivos ficticios."""
from contextlib import closing, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import sqlite3

from taxiroute.copia_sqlite_experimental import _copiar_archivos
from taxiroute.instalacion_inicial import preparar_instalacion
from taxiroute.verificar_copia_previa import verificar_copia_previa, VerificacionCopiaPreviaError


class PruebasVerificarCopiaPrevia(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='taxiroute-prueba-copia-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.raiz = self.base/'instalacion'
        self.rutas = preparar_instalacion(self.raiz, usuario='AdminFicticio', nombre='Prueba',
            correo='prueba@example.invalid', contrasena='Contrasena123!', confirmar_codigos=lambda _: True)
        self.copia = self.base/'copia'
        self.copia.mkdir()
        _copiar_archivos(self.rutas.datos, self.copia)
        (self.copia/'COPIA_DATOS_PREVIA.txt').write_text(
            'TaxiRoute: copia de datos previa comprobada; no es una restauración completa.\n', encoding='utf-8')
        prefijo='taxiroute.verificar_copia_previa.'
        for nombre, opciones in {
            'verificar_permisos_windows': {},
            'instalacion_detenida': {'side_effect':lambda _: nullcontext()},
            'revisar_instalacion': {'return_value':self.rutas},
        }.items():
            setattr(self, nombre, patch(prefijo+nombre, **opciones).start())
        self.addCleanup(patch.stopall)

    def test_copia_igual_verifica_sin_modificar_datos(self):
        antes=(self.copia/'cuentas.sqlite3').read_bytes()
        self.assertTrue(verificar_copia_previa(self.raiz, self.copia))
        self.assertEqual((self.copia/'cuentas.sqlite3').read_bytes(), antes)
        self.instalacion_detenida.assert_called_once()

    def test_cambio_de_datos_es_rechazado(self):
        with closing(sqlite3.connect(self.copia/'cuentas.sqlite3')) as db, db:
            db.execute('UPDATE usuarios SET version_credenciales=version_credenciales+1')
        with self.assertRaises(VerificacionCopiaPreviaError):
            verificar_copia_previa(self.raiz, self.copia)

    def test_tarifas_alteradas_son_rechazadas(self):
        (self.copia/'tarifas.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(VerificacionCopiaPreviaError):
            verificar_copia_previa(self.raiz, self.copia)

    def test_marcador_ausente_o_incompleto_es_rechazado(self):
        marca=self.copia/'COPIA_DATOS_PREVIA.txt'
        for contenido in ('incorrecto', ''):
            marca.write_text(contenido, encoding='utf-8')
            with self.assertRaises(VerificacionCopiaPreviaError):
                verificar_copia_previa(self.raiz, self.copia)
        marca.unlink()
        with self.assertRaises(VerificacionCopiaPreviaError):
            verificar_copia_previa(self.raiz, self.copia)

    def test_permisos_o_instalacion_invalidos_no_fingen_exito(self):
        self.verificar_permisos_windows.side_effect=OSError('ruta privada ficticia')
        with self.assertRaises(VerificacionCopiaPreviaError) as error:
            verificar_copia_previa(self.raiz, self.copia)
        self.assertNotIn('ruta privada', str(error.exception))
        self.instalacion_detenida.assert_not_called()

    def test_instalacion_no_puede_ser_su_propia_copia(self):
        with self.assertRaises(VerificacionCopiaPreviaError):
            verificar_copia_previa(self.raiz, self.rutas.datos)
