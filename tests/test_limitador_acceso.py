"""Límite compartido en memoria, con reloj determinista y sin red."""
from concurrent.futures import ThreadPoolExecutor
import unittest

from taxiroute.limitador_acceso import LimitadorAcceso


class PruebasLimitadorAcceso(unittest.TestCase):
    def test_limita_espera_y_permite_al_caducar_la_ventana(self):
        tiempo = [100.0]
        limite = LimitadorAcceso(maximo=2, periodo=60, reloj=lambda: tiempo[0])
        self.assertEqual(limite.esperar_segundos(), 0)
        tiempo[0] += 10
        self.assertEqual(limite.esperar_segundos(), 0)
        self.assertEqual(limite.esperar_segundos(), 50)
        tiempo[0] += 49.5
        self.assertEqual(limite.esperar_segundos(), 1)
        tiempo[0] += 0.5
        self.assertEqual(limite.esperar_segundos(), 0)
        self.assertEqual(limite.esperar_segundos(), 10)

    def test_intentos_concurrentes_no_superan_el_maximo(self):
        limite = LimitadorAcceso(maximo=4, periodo=60, reloj=lambda: 100.0)
        with ThreadPoolExecutor(max_workers=12) as ejecutor:
            resultados = list(ejecutor.map(lambda _: limite.esperar_segundos(), range(20)))
        self.assertEqual(resultados.count(0), 4)
        self.assertEqual(resultados.count(60), 16)

    def test_parametros_invalidos_se_rechazan(self):
        for opciones in ({"maximo": 0}, {"maximo": True}, {"maximo": 1001},
                         {"periodo": 0}, {"periodo": float("inf")}, {"reloj": None}):
            with self.subTest(opciones=opciones), self.assertRaises((ValueError, TypeError)):
                LimitadorAcceso(**opciones)


if __name__ == "__main__":
    unittest.main()
