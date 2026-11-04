"""Pruebas de cálculo y estados con un reloj controlado."""

from decimal import Decimal
import unittest

from taxiroute.carrera import Carrera, EstadoVehiculo


class RelojControlado:
    def __init__(self):
        self.instante_ns = 0

    def __call__(self):
        return self.instante_ns

    def avanzar_ns(self, nanosegundos):
        self.instante_ns += nanosegundos


class PruebasCarrera(unittest.TestCase):
    def setUp(self):
        self.reloj = RelojControlado()
        self.carrera = Carrera(self.reloj)

    def test_empieza_parado_y_finaliza_sin_tiempo(self):
        self.assertEqual(self.carrera.estado, EstadoVehiculo.PARADO)
        self.assertEqual(self.carrera.finalizar(), Decimal("0.00"))
        self.assertTrue(self.carrera.finalizada)

    def test_tramos_parado_y_movimiento(self):
        self.reloj.avanzar_ns(10_000_000_000)
        self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.reloj.avanzar_ns(20_000_000_000)
        self.assertEqual(self.carrera.finalizar(), Decimal("1.20"))

    def test_consultar_importe_no_duplica_ni_redondea_el_acumulado(self):
        self.reloj.avanzar_ns(200_000_000)  # 0,004 €
        self.assertEqual(self.carrera.importe_actual(), Decimal("0.00"))
        self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.reloj.avanzar_ns(20_000_000)  # 0,001 €; total 0,005 €
        self.assertEqual(self.carrera.finalizar(), Decimal("0.01"))

    def test_cambiar_al_mismo_estado_no_duplica_el_tiempo(self):
        self.reloj.avanzar_ns(1_000_000_000)
        self.carrera.cambiar_estado(EstadoVehiculo.PARADO)
        self.reloj.avanzar_ns(1_000_000_000)
        self.assertEqual(self.carrera.finalizar(), Decimal("0.04"))

    def test_cambios_sucesivos_y_total_provisional(self):
        self.reloj.avanzar_ns(1_000_000_000)
        self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.reloj.avanzar_ns(1_000_000_000)
        self.assertEqual(self.carrera.importe_actual(), Decimal("0.07"))
        self.carrera.cambiar_estado(EstadoVehiculo.PARADO)
        self.reloj.avanzar_ns(1_000_000_000)
        self.assertEqual(self.carrera.finalizar(), Decimal("0.09"))

    def test_no_se_puede_cambiar_ni_finalizar_dos_veces(self):
        self.carrera.finalizar()
        with self.assertRaises(ValueError):
            self.carrera.finalizar()
        with self.assertRaises(ValueError):
            self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.assertEqual(self.carrera.importe_actual(), Decimal("0.00"))

    def test_estado_invalido_no_modifica_la_carrera(self):
        with self.assertRaises(ValueError):
            self.carrera.cambiar_estado("volando")
        self.assertEqual(self.carrera.estado, EstadoVehiculo.PARADO)
        self.reloj.avanzar_ns(1_000_000_000)
        self.assertEqual(self.carrera.finalizar(), Decimal("0.02"))

    def test_reloj_que_retrocede_se_detecta(self):
        self.reloj.avanzar_ns(-1)
        with self.assertRaises(ValueError):
            self.carrera.finalizar()
        self.assertFalse(self.carrera.finalizada)

    def test_carreras_independientes(self):
        self.reloj.avanzar_ns(1_000_000_000)
        self.assertEqual(self.carrera.finalizar(), Decimal("0.02"))
        otra = Carrera(self.reloj)
        self.reloj.avanzar_ns(2_000_000_000)
        self.assertEqual(otra.finalizar(), Decimal("0.04"))


if __name__ == "__main__":
    unittest.main()
