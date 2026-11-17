"""Flujo de transición completo con Docker simulado y datos ficticios."""
from pathlib import Path
from contextlib import closing
import sqlite3
from unittest import TestCase
from unittest.mock import patch

import test_preparacion_actualizacion as fixtures
from taxiroute.copia_sqlite_experimental import _copiar_archivos
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION, ErrorReutilizacion, reutilizar
from taxiroute.transicion_local_red import TransicionLocalRedError, transicionar_local_a_red
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto_red


class PruebasTransicionLocalRed(TestCase):
    setUp = fixtures.PruebasPreparacion.setUp

    def configurar(self):
        with closing(sqlite3.connect(self.rutas.datos / 'cuentas.sqlite3')) as conexion:
            usuario = conexion.execute('SELECT id FROM usuarios').fetchone()[0]
            conexion.execute('INSERT INTO sesiones VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('sesion-ficticia', 'hash-ficticio', usuario, 1, 'administrador', None,
                 '2020-01-01', '2020-01-01', '2099-01-01', None))
            conexion.commit()
        copia = self.raiz.parent / 'copia'
        copia.mkdir()
        _copiar_archivos(self.rutas.datos, copia)
        (copia / 'COPIA_DATOS_PREVIA.txt').write_text(
            'TaxiRoute: copia de datos previa comprobada; no es una restauración completa.\n', encoding='utf-8')
        destino = self.raiz.parent / 'lan'
        self.opciones = dict(origen=self.raiz, copia=copia, destino=destino,
                             direccion='192.168.1.25', puerto=18773, proyecto='taxi-red-ficticio')
        evidencia = EstadoComposicion(
            EstadoContenedor('1'*64, 'sha256:'+'a'*64, 'inicio', 0, True),
            EstadoContenedor('2'*64, 'sha256:'+'b'*64, 'inicio', 0, True))
        parches = {
            'taxiroute.bloquear_transicion_lan.verificar_permisos_windows': {},
            'taxiroute.bloquear_transicion_lan._docker': {'return_value': b''},
            'taxiroute.transicion_local_red.verificar_permisos_windows': {},
            'taxiroute.transicion_local_red.comprobar_direccion_local': {},
            'taxiroute.arranque_red.verificar_permisos_windows': {},
            'taxiroute.arranque_red.comprobar_direccion_local': {},
            'taxiroute.arranque_red._sin_contenedores': {},
            'taxiroute.arranque_red._compose': {},
            'taxiroute.arranque_red._esperar_https': {},
            'taxiroute.arranque_red.verificar_composicion_red': {'return_value': evidencia},
            'taxiroute.arranque_red.verificar_instalacion_red': {'return_value': evidencia},
            'taxiroute.reutilizacion_red.verificar_permisos_windows': {},
            'taxiroute.reutilizacion_red.comprobar_direccion_local': {},
            'taxiroute.reutilizacion_red._verificar_composicion': {'return_value': evidencia},
            'taxiroute.reutilizacion_red.verificar_instalacion_red': {'return_value': evidencia},
            'taxiroute.reutilizacion_red._esperar_https': {},
        }
        for nombre, opciones in parches.items():
            parche = patch(nombre, **opciones)
            parche.start()
            self.addCleanup(parche.stop)
        def docker(args):
            if args[0] == 'ps': return b''
            if args[:2] == ['image', 'inspect']:
                return ('sha256:' + ('a' if '-backend' in args[2] else 'b')*64).encode()
            raise AssertionError(args)
        parche = patch('taxiroute.arranque_red._docker', side_effect=docker)
        parche.start()
        self.addCleanup(parche.stop)
        return evidencia

    def test_transicion_completa_bloquea_local_crea_identidad_y_reutiliza(self):
        esperado = self.configurar()
        resultado = transicionar_local_a_red(**self.opciones)
        self.assertEqual(resultado.evidencia, esperado)
        self.assertTrue((self.identidad / MARCADOR_ACTUALIZACION).is_file())
        manifiesto = leer_manifiesto_red(resultado.identidad)
        self.assertEqual((manifiesto.direccion, manifiesto.puerto), ('192.168.1.25', 18773))
        with closing(sqlite3.connect(resultado.carpeta / 'datos' / 'cuentas.sqlite3')) as conexion:
            self.assertIsNotNone(conexion.execute('SELECT revocada_en FROM sesiones').fetchone()[0])
        with closing(sqlite3.connect(self.rutas.datos / 'cuentas.sqlite3')) as conexion:
            self.assertIsNone(conexion.execute('SELECT revocada_en FROM sesiones').fetchone()[0])
        with self.assertRaises(ErrorReutilizacion):
            reutilizar(self.identidad)

    def test_si_falla_despues_del_bloqueo_conserva_marcador_y_copia(self):
        self.configurar()
        with patch('taxiroute.transicion_local_red._generar_certificado', side_effect=OSError('fallo')):
            with self.assertRaises(TransicionLocalRedError):
                transicionar_local_a_red(**self.opciones)
        self.assertTrue((self.identidad / MARCADOR_ACTUALIZACION).is_file())
        self.assertTrue((self.opciones['copia'] / 'cuentas.sqlite3').is_file())

    def test_destino_existente_no_bloquea_instalacion(self):
        self.configurar()
        self.opciones['destino'].mkdir()
        with self.assertRaises(TransicionLocalRedError):
            transicionar_local_a_red(**self.opciones)
        self.assertFalse((self.identidad / MARCADOR_ACTUALIZACION).exists())

    def test_proyecto_invalido_no_bloquea_instalacion(self):
        self.configurar()
        self.opciones['proyecto'] = 'Nombre Inválido'
        with self.assertRaises(TransicionLocalRedError):
            transicionar_local_a_red(**self.opciones)
        self.assertFalse((self.identidad / MARCADOR_ACTUALIZACION).exists())
