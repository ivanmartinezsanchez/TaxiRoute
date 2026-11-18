from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest
from unittest.mock import patch

from taxiroute.diario_transicion import (ARCHIVO_DIARIO, DiarioTransicionError,
    consultar_diario, iniciar_diario, registrar_estado)


class PruebasDiarioTransicion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix='taxi-diario-')
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name).resolve()

    def test_intento_publicacion_impide_cancelacion(self):
        ident = iniciar_diario(self.carpeta)
        registrar_estado(self.carpeta, ident, 'PUBLICACION_INTENTADA')
        with self.assertRaises(DiarioTransicionError):
            registrar_estado(self.carpeta, ident, 'CANCELACION_SOLICITADA')
        self.assertEqual(consultar_diario(self.carpeta)['estado'], 'PUBLICACION_INTENTADA')
        registrar_estado(self.carpeta, ident, 'PUBLICADA')
        self.assertEqual(consultar_diario(self.carpeta)['estado'], 'PUBLICADA')

    def test_cancelacion_solo_antes_de_publicacion(self):
        ident = iniciar_diario(self.carpeta)
        registrar_estado(self.carpeta, ident, 'CANCELACION_SOLICITADA')
        registrar_estado(self.carpeta, ident, 'CANCELADA')
        with self.assertRaises(DiarioTransicionError):
            registrar_estado(self.carpeta, ident, 'PUBLICACION_INTENTADA')

    def test_diario_truncado_no_autoriza_progreso(self):
        ident = iniciar_diario(self.carpeta)
        ruta = self.carpeta / ARCHIVO_DIARIO
        with ruta.open('ab') as archivo:
            archivo.write(b'{"estado":')
        with self.assertRaises(DiarioTransicionError): consultar_diario(self.carpeta)
        with self.assertRaises(DiarioTransicionError): registrar_estado(self.carpeta, ident, 'PUBLICACION_INTENTADA')

    def test_fsync_fallido_impide_publicar(self):
        ident = iniciar_diario(self.carpeta)
        with patch('taxiroute.diario_transicion.os.fsync', side_effect=OSError('fallo')):
            with self.assertRaises(DiarioTransicionError):
                registrar_estado(self.carpeta, ident, 'PUBLICACION_INTENTADA')
        with self.assertRaises(DiarioTransicionError):
            registrar_estado(self.carpeta, ident, 'CANCELACION_SOLICITADA')

    def test_interrupcion_de_proceso_conserva_el_intento_y_prohibe_cancelar(self):
        codigo = (
            'import os,sys; '
            'from taxiroute.diario_transicion import iniciar_diario,registrar_estado; '
            'i=iniciar_diario(sys.argv[1]); '
            "registrar_estado(sys.argv[1],i,'PUBLICACION_INTENTADA'); "
            'os._exit(19)'
        )
        resultado = subprocess.run([sys.executable, '-c', codigo, str(self.carpeta)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(resultado.returncode, 19)
        registro = consultar_diario(self.carpeta)
        self.assertEqual(registro['estado'], 'PUBLICACION_INTENTADA')
        with self.assertRaises(DiarioTransicionError):
            registrar_estado(self.carpeta, registro['id'], 'CANCELACION_SOLICITADA')

    def test_lanzador_local_bloquea_intento_aun_si_falta_marcador(self):
        from taxiroute.reutilizacion_proxy_experimental import _exigir_sin_actualizacion_pendiente, ErrorReutilizacion
        ident = iniciar_diario(self.carpeta)
        registrar_estado(self.carpeta, ident, 'PUBLICACION_INTENTADA')
        with self.assertRaises(ErrorReutilizacion):
            _exigir_sin_actualizacion_pendiente(self.carpeta)

    def test_lanzador_solo_admite_cancelacion_completa_y_rechaza_diario_roto(self):
        from taxiroute.reutilizacion_proxy_experimental import _exigir_sin_actualizacion_pendiente, ErrorReutilizacion
        ident = iniciar_diario(self.carpeta)
        with self.assertRaises(ErrorReutilizacion): _exigir_sin_actualizacion_pendiente(self.carpeta)
        registrar_estado(self.carpeta, ident, 'CANCELACION_SOLICITADA')
        with self.assertRaises(ErrorReutilizacion): _exigir_sin_actualizacion_pendiente(self.carpeta)
        registrar_estado(self.carpeta, ident, 'CANCELADA')
        _exigir_sin_actualizacion_pendiente(self.carpeta)
        with (self.carpeta / ARCHIVO_DIARIO).open('ab') as archivo: archivo.write(b'parcial')
        with self.assertRaises(ErrorReutilizacion): _exigir_sin_actualizacion_pendiente(self.carpeta)

    def test_diario_existente_no_se_sobrescribe_y_exige_identidad(self):
        ident = iniciar_diario(self.carpeta)
        anterior = (self.carpeta / ARCHIVO_DIARIO).read_bytes()
        with self.assertRaises(DiarioTransicionError): iniciar_diario(self.carpeta)
        with self.assertRaises(DiarioTransicionError): registrar_estado(self.carpeta, 'otro', 'PUBLICACION_INTENTADA')
        self.assertEqual((self.carpeta / ARCHIVO_DIARIO).read_bytes(), anterior)
