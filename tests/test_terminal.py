"""Pruebas de comandos de la interfaz, sin depender del tiempo real."""

import unittest

from taxiroute.carrera import Carrera
from taxiroute.terminal import GestorTerminal


class RelojControlado:
    instante = 0

    def __call__(self):
        return self.instante


class PruebasTerminal(unittest.TestCase):
    def setUp(self):
        self.reloj = RelojControlado()
        self.gestor = GestorTerminal(lambda: Carrera(self.reloj))

    def test_carrera_completa_y_otra_posterior(self):
        self.assertIn("parado", self.gestor.ejecutar("iniciar")[0])
        self.reloj.instante = 10_000_000_000
        self.gestor.ejecutar("mover")
        self.reloj.instante = 30_000_000_000
        self.assertIn("1.20 €", self.gestor.ejecutar("finalizar")[0])
        self.assertIn("iniciada", self.gestor.ejecutar("iniciar")[0])

    def test_no_se_puede_iniciar_dos_veces_ni_salir_con_carrera(self):
        self.gestor.ejecutar("iniciar")
        self.assertIn("Ya hay", self.gestor.ejecutar("iniciar")[0])
        mensaje, salir = self.gestor.ejecutar("salir")
        self.assertIn("Finalízala", mensaje)
        self.assertFalse(salir)
        self.assertIsNotNone(self.gestor.carrera)

    def test_comandos_sin_carrera_y_desconocidos(self):
        for comando in ("mover", "parar", "importe", "finalizar"):
            with self.subTest(comando=comando):
                self.assertIn("No hay", self.gestor.ejecutar(comando)[0])
        self.assertIn("no reconocido", self.gestor.ejecutar("xyz")[0])

    def test_consulta_y_estado_repetido(self):
        self.gestor.ejecutar("iniciar")
        self.reloj.instante = 1_000_000_000
        self.assertIn("0.02 €", self.gestor.estado_actual())
        self.assertIn("ya está", self.gestor.ejecutar("parar")[0])
        self.reloj.instante = 2_000_000_000
        self.assertIn("0.04 €", self.gestor.ejecutar("importe")[0])

    def test_ayuda_y_salida(self):
        self.assertIn("Comandos disponibles", self.gestor.ejecutar(" AYUDA ")[0])
        self.assertTrue(self.gestor.ejecutar("salir")[1])


if __name__ == "__main__":
    unittest.main()
