"""Comprobaciones de recuperación ante fallos de escritura."""
import errno
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.carrera import Carrera
from taxiroute.historial import HistorialCSV
from taxiroute.terminal import GestorTerminal, describir_error


class PruebasErroresGuardado(unittest.TestCase):
    def test_disco_lleno_y_permisos_tienen_mensajes_distintos(self):
        self.assertIn('espacio', describir_error(OSError(errno.ENOSPC, 'lleno')))
        self.assertIn('permiso', describir_error(PermissionError(errno.EACCES, 'acceso')))
        self.assertIn('determinar', describir_error(OSError('desconocido')))

    def test_historial_truncado_no_se_modifica(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / 'historial.csv'
            historial = HistorialCSV(ruta)
            carrera = Carrera()
            carrera.finalizar()
            historial.guardar(carrera.resumen)
            original = ruta.read_bytes()[:-3]
            ruta.write_bytes(original)
            with self.assertRaises(ValueError):
                historial.guardar(carrera.resumen)
            self.assertEqual(ruta.read_bytes(), original)

    def test_exportacion_no_sobrescribe_y_mantiene_pendiente(self):
        class Fallo:
            def guardar(self, resumen):
                raise OSError(errno.ENOSPC, 'lleno')
        with TemporaryDirectory() as carpeta:
            destino = Path(carpeta) / 'Copia Con Mayusculas.csv'
            gestor = GestorTerminal(historial=Fallo())
            gestor.ejecutar('iniciar')
            self.assertIn('espacio', gestor.ejecutar('finalizar')[0])
            self.assertIn('Copia creada', gestor.ejecutar('exportar ' + str(destino))[0])
            self.assertEqual(len(HistorialCSV(destino).consultar()), 1)
            self.assertIsNotNone(gestor.pendiente)
            anterior = destino.read_bytes()
            self.assertIn('Ya existe', gestor.ejecutar('exportar ' + str(destino))[0])
            self.assertEqual(destino.read_bytes(), anterior)

    def test_csv_malformado_muestra_error_sin_crash(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / 'historial.csv'
            ruta.write_bytes(b'\xff\n')
            gestor = GestorTerminal(historial=HistorialCSV(ruta))
            self.assertIn('No se puede consultar', gestor.ejecutar('historial')[0])
