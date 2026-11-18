"""Compatibilidad experimental del resumen pendiente con el historial CSV."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import inicializar_base
from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import finalizar_punto_guardado, resumen_para_historial
from taxiroute.historial import HistorialCSV
from taxiroute.puntos_control import PuntoControlInvalido, guardar_punto, punto_de_captura


class PruebasResumenHistorial(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.ruta = self.carpeta / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.cuenta = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.tiempo = 0
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.carrera = Carrera(reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)

    def avanzar(self, segundos):
        self.tiempo += segundos * 1_000_000_000
        self.fecha += timedelta(seconds=segundos)

    def pendiente_ejemplo(self):
        self.avanzar(10)
        self.carrera.cambiar_estado(EstadoVehiculo.EN_MOVIMIENTO)
        self.avanzar(20)
        punto = punto_de_captura(self.carrera.capturar(), self.cuenta.id, 2)
        guardar_punto(self.ruta, punto)
        return finalizar_punto_guardado(self.ruta, punto, punto.ultimo_control)

    def test_conversion_coincide_con_calculo_normal_y_csv_acepta_reintento(self):
        pendiente = self.pendiente_ejemplo()
        convertido = resumen_para_historial(pendiente)
        self.carrera.finalizar()
        self.assertEqual(convertido, self.carrera.resumen)
        historial = HistorialCSV(self.carpeta / "historial.csv")
        historial.guardar(convertido)
        historial.guardar(convertido)
        filas = historial.consultar()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["identificador"], convertido.identificador)
        self.assertEqual(filas[0]["importe"], "1.20")

    def test_importes_equivalentes_se_normalizan_para_reintento_csv(self):
        pendiente = self.pendiente_ejemplo()
        normal = resumen_para_historial(pendiente)
        con_ceros = resumen_para_historial(replace(pendiente, importe=Decimal("1.200")))
        self.assertEqual(str(con_ceros.importe), "1.20")
        historial = HistorialCSV(self.carpeta / "historial.csv")
        historial.guardar(normal)
        historial.guardar(con_ceros)
        self.assertEqual(len(historial.consultar()), 1)
    def test_rechaza_importes_y_fechas_incoherentes(self):
        pendiente = self.pendiente_ejemplo()
        for alterado in (replace(pendiente, importe=Decimal("1.21")),
                         replace(pendiente, importe=Decimal("NaN")),
                         replace(pendiente, fin=pendiente.fin + timedelta(seconds=1)),
                         replace(pendiente, fin=pendiente.fin.replace(tzinfo=None))):
            with self.subTest(alterado=alterado), self.assertRaises(PuntoControlInvalido):
                resumen_para_historial(alterado)