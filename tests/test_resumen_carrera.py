"""Datos necesarios para el historial de carreras."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from taxiroute.carrera import Carrera, EstadoVehiculo


class PruebasResumenCarrera(unittest.TestCase):
    def test_resumen_conserva_tiempos_tarifas_y_fechas(self):
        instante = [0]
        fechas = iter([datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc),
                       datetime(2026, 11, 5, 10, 0, 3, tzinfo=timezone.utc)])
        carrera = Carrera(lambda: instante[0], lambda: next(fechas))
        self.assertIsNone(carrera.resumen)
        instante[0] = 1_500_000_000
        carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        instante[0] = 3_000_000_000
        self.assertEqual(carrera.finalizar(), Decimal("0.11"))
        resumen = carrera.resumen
        self.assertTrue(resumen.identificador)
        self.assertEqual(resumen.inicio, datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(resumen.fin, datetime(2026, 11, 5, 10, 0, 3, tzinfo=timezone.utc))
        self.assertEqual(resumen.segundos_parado, Decimal("1.5"))
        self.assertEqual(resumen.segundos_movimiento, Decimal("1.5"))
        self.assertEqual(resumen.duracion_segundos, Decimal("3.0"))
        self.assertEqual(resumen.tarifa_parado, Decimal("0.02"))
        self.assertEqual(resumen.tarifa_movimiento, Decimal("0.05"))
        self.assertEqual(resumen.importe, Decimal("0.11"))

    def test_tarifas_se_fijan_al_iniciar(self):
        reloj = [0]
        carrera = Carrera(lambda: reloj[0])
        originales = Carrera.TARIFAS
        try:
            Carrera.TARIFAS = {EstadoVehiculo.PARADO: Decimal("10"),
                                EstadoVehiculo.EN_MOVIMIENTO: Decimal("20")}
            reloj[0] = 1_000_000_000
            self.assertEqual(carrera.finalizar(), Decimal("0.02"))
            self.assertEqual(carrera.resumen.tarifa_parado, Decimal("0.02"))
        finally:
            Carrera.TARIFAS = originales

    def test_identificadores_son_distintos(self):
        a, b = Carrera(), Carrera()
        a.finalizar()
        b.finalizar()
        self.assertNotEqual(a.resumen.identificador, b.resumen.identificador)


if __name__ == "__main__":
    unittest.main()
