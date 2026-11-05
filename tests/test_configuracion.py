"""Pruebas de configuración de tarifas y cambios entre carreras."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.configuracion import ConfiguracionError, cargar_tarifas
from taxiroute.terminal import GestorTerminal


class PruebasTarifasConfigurables(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.ruta = Path(self.temporal.name) / 'tarifas.json'

    def escribir(self, contenido):
        self.ruta.write_text(contenido, encoding='utf-8')

    def test_lectura_exacta_de_decimales(self):
        self.escribir('{"parado":"0.02","en_movimiento":"0.05"}')
        tarifas = cargar_tarifas(self.ruta)
        self.assertEqual(tarifas[EstadoVehiculo.PARADO], Decimal('0.02'))
        self.assertEqual(tarifas[EstadoVehiculo.EN_MOVIMIENTO], Decimal('0.05'))

    def test_limites_de_cifras_de_tarifas(self):
        self.escribir('{"parado":"999999999999.123456789","en_movimiento":"0.05"}')
        self.assertEqual(cargar_tarifas(self.ruta)[EstadoVehiculo.PARADO], Decimal('999999999999.123456789'))
        for valor in ('1000000000000', '0.1234567890', '9' * 40):
            with self.subTest(valor=valor):
                self.escribir('{"parado":"' + valor + '","en_movimiento":"0.05"}')
                with self.assertRaisesRegex(ConfiguracionError, '12 cifras enteras y 9 decimales'):
                    cargar_tarifas(self.ruta)

    def test_configuracion_ausente_o_invalida(self):
        with self.assertRaises(ConfiguracionError):
            cargar_tarifas(self.ruta)
        for texto in ('{', '{}', '{"parado":"-1","en_movimiento":"0.05"}',
                      '{"parado":"NaN","en_movimiento":"0.05"}',
                      '{"parado":0.02,"en_movimiento":"0.05"}',
                      '{"parado":"0.02","parado":"0.03","en_movimiento":"0.05"}',
                      '{"parado":"0.02","en_movimiento":"0.05","extra":"1"}'):
            with self.subTest(texto=texto):
                self.escribir(texto)
                with self.assertRaises(ConfiguracionError):
                    cargar_tarifas(self.ruta)

    def test_cambios_solo_afectan_a_la_siguiente_carrera(self):
        instante = [0]
        self.escribir('{"parado":"0.02","en_movimiento":"0.05"}')
        gestor = GestorTerminal(lambda: Carrera(lambda: instante[0], tarifas=cargar_tarifas(self.ruta)))
        self.assertIn('iniciada', gestor.ejecutar('iniciar')[0])
        self.escribir('{"parado":"0.03","en_movimiento":"0.06"}')
        instante[0] = 10_000_000_000
        self.assertIn('0.20 €', gestor.ejecutar('finalizar')[0])
        self.assertIn('iniciada', gestor.ejecutar('iniciar')[0])
        instante[0] = 20_000_000_000
        self.assertIn('0.30 €', gestor.ejecutar('finalizar')[0])

    def test_error_impide_iniciar_y_permanece_activa_la_aplicacion(self):
        gestor = GestorTerminal(lambda: Carrera(tarifas=cargar_tarifas(self.ruta)))
        mensaje, salir = gestor.ejecutar('iniciar')
        self.assertIn('tarifas', mensaje.lower())
        self.assertFalse(salir)
        self.assertIsNone(gestor.carrera)
        self.escribir('{"parado":"0.02","en_movimiento":"0.05"}')
        self.assertIn('iniciada', gestor.ejecutar('iniciar')[0])
