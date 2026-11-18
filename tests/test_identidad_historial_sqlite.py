from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError
from taxiroute.identidad_historial_sqlite import (
    IdentidadHistorialError, leer_identidad_historial, preparar_identidad_historial,
)


class PruebasIdentidadHistorial(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / 'historial.sqlite3'
        HistorialSQLite(self.ruta).inicializar()

    def test_identidad_estable_y_distinta_por_base(self):
        self.assertIsNone(leer_identidad_historial(self.ruta))
        primera = preparar_identidad_historial(self.ruta)
        self.assertEqual(str(UUID(primera)), primera)
        self.assertEqual(preparar_identidad_historial(self.ruta), primera)
        self.assertEqual(leer_identidad_historial(self.ruta), primera)
        otra = self.ruta.with_name('otra.sqlite3')
        HistorialSQLite(otra).inicializar()
        self.assertNotEqual(preparar_identidad_historial(otra), primera)

    def test_no_crea_una_base_ausente_ni_adopta_una_ajena(self):
        ausente = self.ruta.with_name('ausente.sqlite3')
        with self.assertRaises(IdentidadHistorialError):
            preparar_identidad_historial(ausente)
        self.assertFalse(ausente.exists())
        ajena = self.ruta.with_name('ajena.sqlite3')
        with closing(sqlite3.connect(ajena)):
            pass
        with self.assertRaises(HistorialSQLiteError):
            preparar_identidad_historial(ajena)

    def test_rechaza_tabla_inconsistente_sin_reemplazarla(self):
        with closing(sqlite3.connect(self.ruta)) as conexion, conexion:
            conexion.execute('CREATE TABLE identidad_historial (id INTEGER, identificador TEXT)')
            conexion.execute("INSERT INTO identidad_historial VALUES (1, 'identidad-invalida')")
        with self.assertRaises(IdentidadHistorialError):
            preparar_identidad_historial(self.ruta)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            self.assertEqual(conexion.execute('SELECT identificador FROM identidad_historial').fetchone()[0], 'identidad-invalida')

    def test_preparar_conserva_carreras_existentes(self):
        valores = ('carrera-prueba', 'inicio', 'fin', '2', '2', '0', '0.02', '0.05', '0.04', 'cuenta-prueba')
        with closing(sqlite3.connect(self.ruta)) as conexion, conexion:
            conexion.execute('INSERT INTO carreras (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?)', valores)
        preparar_identidad_historial(self.ruta)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            fila = conexion.execute('SELECT identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id FROM carreras').fetchone()
        self.assertEqual(fila, valores)

    def test_inicializacion_concurrente_conserva_una_identidad(self):
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(preparar_identidad_historial, [self.ruta, self.ruta]))
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(leer_identidad_historial(self.ruta), resultados[0])


if __name__ == '__main__':
    unittest.main()
