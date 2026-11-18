"""Capturas de carreras en curso con relojes controlados y base temporal."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import inicializar_base
from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.cuentas import crear_cuenta
from taxiroute.puntos_control import punto_de_captura, guardar_punto, leer_punto


class PruebasCapturaCarrera(unittest.TestCase):
    def setUp(self):
        self.nanosegundos = 0
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.carrera = Carrera(reloj=lambda: self.nanosegundos, fecha_actual=lambda: self.fecha)

    def avanzar(self, segundos):
        self.nanosegundos += segundos * 1_000_000_000
        self.fecha += timedelta(seconds=segundos)

    def test_captura_incluye_tramos_y_no_interrumpe_ni_duplica(self):
        self.avanzar(10)
        primera = self.carrera.capturar()
        self.assertEqual((primera.parado_ns, primera.movimiento_ns), (10_000_000_000, 0))
        self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.avanzar(20)
        segunda = self.carrera.capturar()
        self.assertEqual((segunda.parado_ns, segunda.movimiento_ns), (10_000_000_000, 20_000_000_000))
        self.assertEqual(segunda.estado, EstadoVehiculo.EN_MOVIMIENTO)
        self.assertEqual(segunda.identificador, primera.identificador)
        with self.assertRaises(FrozenInstanceError):
            segunda.parado_ns = 0
        self.avanzar(10)
        self.assertEqual(self.carrera.finalizar(), Decimal("1.70"))
        self.assertEqual(self.carrera.resumen.segundos_movimiento, Decimal("30"))

    def test_guardar_captura_real_y_leer_la_misma_informacion(self):
        with TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "cuentas.sqlite3"
            inicializar_base(ruta)
            usuario = crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
            self.avanzar(5)
            punto = punto_de_captura(self.carrera.capturar(), usuario.id, 0)
            guardar_punto(ruta, punto)
            self.assertEqual(leer_punto(ruta, usuario.id, punto.carrera_id), punto)
            self.assertEqual(punto.parado_ns, 5_000_000_000)
            self.assertEqual(punto.tarifa_parado, "0.02")
            self.avanzar(5)
            self.assertEqual(self.carrera.finalizar(), Decimal("0.20"))

    def test_tarifas_pequenas_se_guardan_en_formato_decimal(self):
        tarifas = {EstadoVehiculo.PARADO: Decimal("0.000000001"),
                   EstadoVehiculo.EN_MOVIMIENTO: Decimal("0.05")}
        carrera = Carrera(reloj=lambda: self.nanosegundos, fecha_actual=lambda: self.fecha, tarifas=tarifas)
        punto = punto_de_captura(carrera.capturar(), "usuario", 0)
        self.assertEqual(punto.tarifa_parado, "0.000000001")

    def test_captura_rechaza_reloj_retrocedido_y_carrera_finalizada(self):
        self.nanosegundos = -1
        with self.assertRaises(ValueError):
            self.carrera.capturar()
        self.nanosegundos = 0
        self.carrera.finalizar()
        with self.assertRaises(ValueError):
            self.carrera.capturar()