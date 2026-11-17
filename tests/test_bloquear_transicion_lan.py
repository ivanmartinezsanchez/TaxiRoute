"""Transición local: pruebas de copia y barrera, con Docker simulado."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from taxiroute.bloquear_transicion_lan import TransicionRedError, bloquear_local_para_transicion
from taxiroute.copia_sqlite_experimental import _copiar_archivos
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION, ErrorReutilizacion, reutilizar
import test_preparacion_actualizacion as fixtures


class PruebasBloqueoTransicion(TestCase):
    setUp = fixtures.PruebasPreparacion.setUp

    def preparar_copia(self):
        copia = self.raiz.parent / 'respaldo'
        copia.mkdir()
        _copiar_archivos(self.rutas.datos, copia)
        (copia / 'COPIA_DATOS_PREVIA.txt').write_text(
            'TaxiRoute: copia de datos previa comprobada; no es una restauración completa.\n', encoding='utf-8')
        self.p_acl = patch('taxiroute.bloquear_transicion_lan.verificar_permisos_windows')
        self.p_docker = patch('taxiroute.bloquear_transicion_lan._docker', return_value=b'')
        self.p_acl.start()
        self.p_docker.start()
        self.addCleanup(self.p_acl.stop)
        self.addCleanup(self.p_docker.stop)
        return copia

    def test_copia_valida_bloquea_arranque_local_y_preserva_datos(self):
        copia = self.preparar_copia()
        marcador = bloquear_local_para_transicion(self.raiz, copia)
        self.assertEqual(marcador, self.identidad / MARCADOR_ACTUALIZACION)
        self.assertTrue(marcador.is_file())
        proveedor = Mock()
        with self.assertRaises(ErrorReutilizacion):
            reutilizar(self.identidad, proveedor=proveedor)
        proveedor.assert_not_called()
        self.assertTrue((self.rutas.datos / 'cuentas.sqlite3').is_file())

    def test_copia_alterada_no_bloquea(self):
        copia = self.preparar_copia()
        (copia / 'tarifas.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(TransicionRedError):
            bloquear_local_para_transicion(self.raiz, copia)
        self.assertFalse((self.identidad / MARCADOR_ACTUALIZACION).exists())

    def test_docker_activo_no_bloquea(self):
        copia = self.preparar_copia()
        self.docker.return_value = b'activo'
        with self.assertRaises(TransicionRedError):
            bloquear_local_para_transicion(self.raiz, copia)
        self.assertFalse((self.identidad / MARCADOR_ACTUALIZACION).exists())

    def test_copia_sin_marcador_no_bloquea(self):
        copia = self.preparar_copia()
        (copia / 'COPIA_DATOS_PREVIA.txt').unlink()
        with self.assertRaises(TransicionRedError):
            bloquear_local_para_transicion(self.raiz, copia)
        self.assertFalse((self.identidad / MARCADOR_ACTUALIZACION).exists())
