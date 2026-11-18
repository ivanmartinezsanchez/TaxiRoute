"""Consulta de carreras por el día local de finalización."""

from datetime import date, timedelta, timezone
from unittest.mock import patch
import unittest

from taxiroute.terminal import GestorTerminal


class HistorialPrueba:
    def __init__(self, filas):
        self.filas = filas

    def consultar(self):
        return self.filas


def fila(identificador, inicio, fin):
    return {"identificador": identificador, "inicio": inicio, "fin": fin,
            "duracion_segundos": "120", "importe": "1.20"}


class PruebasHistorialHoy(unittest.TestCase):
    def setUp(self):
        self.filas = [
            fila("ayer", "2026-01-04T21:00:00+00:00", "2026-01-04T22:59:00+00:00"),
            fila("medianoche", "2026-01-04T22:55:00+00:00", "2026-01-04T23:10:00+00:00"),
            fila("carrera_diurna", "2026-01-05T09:00:00+00:00", "2026-01-05T10:00:00+00:00"),
            fila("manana", "2026-01-05T22:30:00+00:00", "2026-01-05T23:01:00+00:00"),
        ]
        self.gestor = GestorTerminal(historial=HistorialPrueba(self.filas))
        self.zona = timezone(timedelta(hours=1))

    def test_filtra_por_fecha_local_de_finalizacion(self):
        with patch('taxiroute.servicio.fecha_local_actual', return_value=date(2026, 1, 5)), \
             patch('taxiroute.servicio.a_fecha_local', side_effect=lambda dt: dt.astimezone(self.zona).date()):
            salida, _ = self.gestor.ejecutar('historial hoy')
        self.assertIn('medianoche', salida)
        self.assertIn('carrera_diurna', salida)
        self.assertNotIn('ayer', salida)
        self.assertNotIn('manana', salida)

    def test_historial_completo_conserva_todas_las_carreras(self):
        salida, _ = self.gestor.ejecutar('historial')
        for identificador in ('ayer', 'medianoche', 'carrera_diurna', 'manana'):
            self.assertIn(identificador, salida)

    def test_sin_carreras_hoy_muestra_mensaje(self):
        with patch('taxiroute.servicio.fecha_local_actual', return_value=date(2030, 1, 1)):
            salida, _ = self.gestor.ejecutar('historial hoy')
        self.assertIn('No hay carreras finalizadas hoy', salida)

    def test_fecha_invalida_o_sin_zona_no_se_omite_sin_aviso(self):
        for fin in ('fecha-incorrecta', '2026-01-05T10:00:00'):
            with self.subTest(fin=fin):
                gestor = GestorTerminal(historial=HistorialPrueba([fila('x', 'x', fin)]))
                salida, _ = gestor.ejecutar('historial hoy')
                self.assertIn('fecha de finalización', salida)

    def test_argumento_desconocido_no_se_acepta(self):
        salida, _ = self.gestor.ejecutar('historial mañana')
        self.assertIn('Comando no reconocido', salida)
