"""Pruebas del historial persistente."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from taxiroute.carrera import Carrera
from taxiroute.historial import HistorialCSV
from taxiroute.terminal import GestorTerminal


class PruebasHistorial(unittest.TestCase):
    def test_guarda_y_recupera_sin_duplicar(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / 'datos' / 'carreras.csv'
            gestor = GestorTerminal(historial=HistorialCSV(ruta))
            gestor.ejecutar('iniciar')
            self.assertIn('guardada', gestor.ejecutar('finalizar')[0])
            datos = HistorialCSV(ruta).consultar()
            self.assertEqual(len(datos), 1)
            self.assertIn(datos[0]['identificador'], gestor.ejecutar('historial')[0])
            otra = Carrera()
            otra.finalizar()
            historial = HistorialCSV(ruta)
            historial.guardar(otra.resumen)
            historial.guardar(otra.resumen)
            self.assertEqual(len(historial.consultar()), 2)

    def test_error_retiene_carrera_y_reintenta(self):
        class HistorialConError:
            fallar = True
            def guardar(self, resumen):
                if self.fallar:
                    raise OSError('disco lleno')
        historial = HistorialConError()
        gestor = GestorTerminal(historial=historial)
        gestor.ejecutar('iniciar')
        self.assertIn('disco lleno', gestor.ejecutar('finalizar')[0])
        self.assertIsNotNone(gestor.pendiente)
        self.assertIn('pendiente', gestor.ejecutar('iniciar')[0])
        self.assertFalse(gestor.ejecutar('salir')[1])
        historial.fallar = False
        self.assertIn('guardada', gestor.ejecutar('reintentar')[0])
        self.assertIsNone(gestor.pendiente)
        self.assertIn('iniciada', gestor.ejecutar('iniciar')[0])
