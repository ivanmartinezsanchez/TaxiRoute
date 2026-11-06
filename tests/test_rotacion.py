"""Pruebas de rotación de registros operativos."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.registro import RegistroActividad


class PruebasRotacion(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ruta = Path(self.temp.name) / 'actividad.jsonl'
        self.registro = RegistroActividad(self.ruta, max_bytes=1, copias=2)

    def test_rotacion_mantiene_orden_y_elimina_solo_la_copia_mas_antigua(self):
        for evento in ('uno', 'dos', 'tres', 'cuatro'):
            self.assertIsNone(self.registro.registrar(evento))
        actual = json.loads(self.ruta.read_text(encoding='utf-8'))
        copia1 = json.loads(self.ruta.with_name(self.ruta.name + '.1').read_text(encoding='utf-8'))
        copia2 = json.loads(self.ruta.with_name(self.ruta.name + '.2').read_text(encoding='utf-8'))
        self.assertEqual((actual['evento'], copia1['evento'], copia2['evento']), ('cuatro', 'tres', 'dos'))
        self.assertFalse(self.ruta.with_name(self.ruta.name + '.3').exists())

    def test_error_al_rotar_avisa_y_conserva_archivo_actual(self):
        self.assertIsNone(self.registro.registrar('primero'))
        antes = self.ruta.read_bytes()
        with patch('taxiroute.registro.os.replace', side_effect=PermissionError('archivo ocupado')):
            aviso = self.registro.registrar('segundo')
        self.assertIn('no se están pudiendo guardar', aviso)
        self.assertEqual(self.ruta.read_bytes(), antes)
        self.assertIn('vuelve a estar disponible', self.registro.registrar('tercero'))
        self.assertEqual(json.loads(self.ruta.read_text(encoding='utf-8'))['evento'], 'tercero')

    def test_limite_se_mide_en_bytes_incluidos_caracteres_no_ascii(self):
        registro = RegistroActividad(self.ruta, max_bytes=4096, copias=2)
        instante = datetime(2026, 11, 6, tzinfo=timezone.utc)
        with patch('taxiroute.registro.datetime') as reloj:
            reloj.now.return_value = instante
            self.assertIsNone(registro.registrar('estación'))
            tamano_primera = self.ruta.stat().st_size
            registro.max_bytes = tamano_primera * 2 - 1
            self.assertIsNone(registro.registrar('estación'))
        copia = self.ruta.with_name(self.ruta.name + '.1')
        self.assertEqual(copia.stat().st_size, tamano_primera)
        self.assertEqual(json.loads(copia.read_text(encoding='utf-8'))['evento'], 'estación')
        self.assertEqual(json.loads(self.ruta.read_text(encoding='utf-8'))['evento'], 'estación')

    def test_si_no_alcanza_limite_no_rota(self):
        registro = RegistroActividad(self.ruta, max_bytes=4096, copias=2)
        registro.registrar('uno')
        registro.registrar('dos')
        eventos = [json.loads(linea)['evento'] for linea in self.ruta.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(eventos, ['uno', 'dos'])
        self.assertFalse(self.ruta.with_name(self.ruta.name + '.1').exists())
