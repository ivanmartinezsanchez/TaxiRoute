"""Comprobaciones de los registros operativos."""

from contextlib import redirect_stdout
from io import StringIO
import errno
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.registro import RegistroActividad
from taxiroute.terminal import GestorTerminal, main


class PruebasRegistro(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.ruta = Path(self.temporal.name) / 'actividad.jsonl'
        self.registro = RegistroActividad(self.ruta)

    def eventos(self):
        return [json.loads(linea) for linea in self.ruta.read_text(encoding='utf-8').splitlines()]

    def test_eventos_son_json_y_se_guardan_en_orden(self):
        self.assertIsNone(self.registro.registrar('aplicacion_iniciada'))
        self.assertIsNone(self.registro.registrar('estado_cambiado', estado='parado'))
        eventos = self.eventos()
        self.assertEqual([e['evento'] for e in eventos], ['aplicacion_iniciada', 'estado_cambiado'])
        self.assertEqual(eventos[0]['nivel'], 'INFO')
        self.assertTrue(eventos[0]['fecha'].endswith('+00:00'))
        self.assertEqual(eventos[1]['estado'], 'parado')

    def test_fallo_y_recuperacion_avisados_sin_perder_carrera(self):
        bloqueador = Path(self.temporal.name) / 'archivo'
        bloqueador.write_text('x', encoding='utf-8')
        self.registro.ruta = bloqueador / 'actividad.jsonl'
        gestor = GestorTerminal(registro=self.registro)
        mensaje, _ = gestor.ejecutar('iniciar')
        self.assertIn('Carrera iniciada', mensaje)
        self.assertIn('no se están pudiendo guardar', mensaje)
        self.assertIsNotNone(gestor.carrera)
        self.registro.ruta = self.ruta
        mensaje, _ = gestor.ejecutar('mover')
        self.assertIn('vuelve a estar disponible', mensaje)
        self.assertIn('no quedaron registrados', mensaje)
        self.assertEqual(self.eventos()[0]['evento'], 'estado_cambiado')

    def test_eventos_no_incluyen_comandos_desconocidos(self):
        gestor = GestorTerminal(registro=self.registro)
        gestor.ejecutar('iniciar')
        gestor.ejecutar('mover')
        gestor.ejecutar('texto privado de prueba')
        gestor.ejecutar('finalizar')
        contenido = self.ruta.read_text(encoding='utf-8')
        self.assertNotIn('texto privado de prueba', contenido)
        eventos = [e['evento'] for e in self.eventos()]
        self.assertEqual(eventos, ['carrera_iniciada', 'estado_cambiado', 'carrera_finalizada'])

    def test_error_guardado_queda_registrado_sin_exponer_el_mensaje(self):
        class HistorialConError:
            def guardar(self, resumen):
                raise OSError(errno.ENOSPC, 'ruta privada de ejemplo')
        gestor = GestorTerminal(historial=HistorialConError(), registro=self.registro)
        gestor.ejecutar('iniciar')
        gestor.ejecutar('finalizar')
        evento = self.eventos()[-1]
        self.assertEqual(evento['evento'], 'error_guardado_historial')
        self.assertEqual(evento['nivel'], 'ERROR')
        self.assertEqual(evento['tipo'], 'OSError')
        self.assertEqual(evento['errno'], errno.ENOSPC)
        self.assertNotIn('ruta privada de ejemplo', json.dumps(evento))

    def test_entrada_principal_registra_inicio_y_salida(self):
        class SesionFalsa:
            def __init__(self, **kwargs):
                self.comandos = iter(['iniciar', 'mover', 'finalizar', 'salir'])
            def prompt(self, texto):
                return next(self.comandos)
        with patch('taxiroute.terminal.Path.home', return_value=Path(self.temporal.name)), \
             patch('taxiroute.terminal.PromptSession', SesionFalsa), \
             redirect_stdout(StringIO()):
            main()
        ruta = Path(self.temporal.name) / '.taxiroute' / 'actividad.jsonl'
        eventos = [json.loads(linea)['evento'] for linea in ruta.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(eventos, ['aplicacion_iniciada', 'carrera_iniciada',
                                   'estado_cambiado', 'carrera_finalizada',
                                   'historial_guardado', 'aplicacion_finalizada'])

    def test_registro_truncado_se_conserva_y_no_se_da_por_guardado(self):
        self.ruta.write_bytes(b'{"evento":"incompleto"')
        antes = self.ruta.read_bytes()
        aviso = self.registro.registrar('carrera_iniciada')
        self.assertIn('no se están pudiendo guardar', aviso)
        self.assertEqual(self.ruta.read_bytes(), antes)
