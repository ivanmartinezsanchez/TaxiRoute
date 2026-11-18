"""Validación de servicios independientes; no acredita autenticación ni flota web."""
from decimal import Decimal
import errno
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.configuracion import cargar_tarifas, guardar_tarifas
from taxiroute.historial import HistorialCSV
from taxiroute.servicio import ServicioCarreras


class PruebasServiciosIndependientes(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.ruta_tarifas = self.carpeta / "tarifas.json"
        guardar_tarifas(self.ruta_tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
        self.tiempo = 0
        self.historial = HistorialCSV(self.carpeta / "historial.csv")
        self.a = self.crear_servicio()
        self.b = self.crear_servicio()

    def crear_servicio(self):
        return ServicioCarreras(
            crear_carrera=lambda: Carrera(
                reloj=lambda: self.tiempo,
                tarifas=cargar_tarifas(self.ruta_tarifas),
            ),
            historial=self.historial,
        )

    def test_estados_y_finalizacion_no_se_mezclan(self):
        self.a.ejecutar("iniciar")
        self.b.ejecutar("iniciar")
        self.tiempo = 10_000_000_000
        self.a.ejecutar("mover")
        self.assertEqual(self.b.carrera.estado, EstadoVehiculo.PARADO)
        self.tiempo = 20_000_000_000
        self.a.ejecutar("finalizar")
        self.assertEqual(self.a.ultimo_resumen.importe, Decimal("0.70"))
        self.assertIsNotNone(self.b.carrera)
        self.assertEqual(self.b.carrera.importe_actual(), Decimal("0.40"))
        self.b.ejecutar("finalizar")
        filas = self.historial.consultar()
        self.assertEqual(len(filas), 2)
        self.assertEqual({f["importe"] for f in filas}, {"0.70", "0.40"})
        self.assertEqual(len({f["identificador"] for f in filas}), 2)

    def test_nuevas_tarifas_no_esperan_al_fin_de_otro_vehiculo(self):
        self.a.ejecutar("iniciar")
        self.b.ejecutar("iniciar")
        self.tiempo = 10_000_000_000
        guardar_tarifas(self.ruta_tarifas, {"parado": "0.03", "en_movimiento": "0.06"})
        self.a.ejecutar("finalizar")
        self.assertEqual(self.a.ultimo_resumen.importe, Decimal("0.20"))
        self.a.ejecutar("iniciar")
        self.tiempo = 20_000_000_000
        self.a.ejecutar("finalizar")
        self.b.ejecutar("finalizar")
        self.assertEqual(self.a.ultimo_resumen.importe, Decimal("0.30"))
        self.assertEqual(self.b.ultimo_resumen.importe, Decimal("0.40"))
        self.assertEqual(self.b.ultimo_resumen.tarifa_parado, Decimal("0.02"))
        self.assertEqual(len(self.historial.consultar()), 3)

    def test_guardado_pendiente_no_bloquea_otro_servicio(self):
        self.a.ejecutar("iniciar")
        self.b.ejecutar("iniciar")
        self.tiempo = 10_000_000_000
        with patch.object(self.historial, "guardar", side_effect=OSError(errno.ENOSPC, "simulado")):
            self.a.ejecutar("finalizar")
        pendiente = self.a.pendiente
        self.assertIsNotNone(pendiente)
        self.a.ejecutar("iniciar")
        self.assertIsNone(self.a.carrera)
        self.b.ejecutar("mover")
        self.tiempo = 20_000_000_000
        self.b.ejecutar("finalizar")
        self.assertIsNone(self.b.pendiente)
        self.assertIs(self.a.pendiente, pendiente)
        self.assertEqual(len(self.historial.consultar()), 1)
        self.a.ejecutar("reintentar")
        self.a.ejecutar("reintentar")
        self.assertIsNone(self.a.pendiente)
        filas = self.historial.consultar()
        self.assertEqual(len(filas), 2)
        self.assertEqual(sum(f["identificador"] == pendiente.identificador for f in filas), 1)
