"""Transición local: pruebas de copia y barrera, con Docker simulado."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from taxiroute.bloquear_transicion_lan import TransicionRedError, bloquear_local_para_transicion, restaurar_local_antes_de_publicar
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
        self.docker_bloqueo = self.p_docker.start()
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

    def test_recupera_local_si_datos_y_servicios_no_cambiaron(self):
        copia = self.preparar_copia()
        marcador = bloquear_local_para_transicion(self.raiz, copia)
        info = marcador.stat()
        with patch('taxiroute.bloquear_transicion_lan.leer_manifiesto', return_value=self.manifiesto), \
             patch('taxiroute.bloquear_transicion_lan.verificar_para_reutilizar', return_value=fixtures.estado()):
            self.assertTrue(restaurar_local_antes_de_publicar(self.raiz, copia, (info.st_dev, info.st_ino)))
        self.assertFalse(marcador.exists())
        self.assertTrue((copia / 'cuentas.sqlite3').exists())

    def test_no_recupera_si_aparece_otro_contenedor(self):
        copia = self.preparar_copia()
        marcador = bloquear_local_para_transicion(self.raiz, copia)
        info = marcador.stat()
        self.docker_bloqueo.return_value = b'activo'
        with patch('taxiroute.bloquear_transicion_lan.leer_manifiesto', return_value=self.manifiesto), \
             patch('taxiroute.bloquear_transicion_lan.verificar_para_reutilizar', return_value=fixtures.estado()), \
             self.assertRaises(TransicionRedError):
            restaurar_local_antes_de_publicar(self.raiz, copia, (info.st_dev, info.st_ino))
        self.assertTrue(marcador.exists())

    def test_no_recupera_si_copia_cambia(self):
        copia = self.preparar_copia()
        marcador = bloquear_local_para_transicion(self.raiz, copia)
        info = marcador.stat()
        (copia / 'tarifas.json').write_text('{}', encoding='utf-8')
        with patch('taxiroute.bloquear_transicion_lan.leer_manifiesto', return_value=self.manifiesto), \
             patch('taxiroute.bloquear_transicion_lan.verificar_para_reutilizar', return_value=fixtures.estado()), \
             self.assertRaises(TransicionRedError):
            restaurar_local_antes_de_publicar(self.raiz, copia, (info.st_dev, info.st_ino))
        self.assertTrue(marcador.exists())
