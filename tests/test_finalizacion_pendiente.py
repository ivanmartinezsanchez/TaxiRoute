"""Finalización pendiente aislada, con cuentas y bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import finalizar_punto_guardado
from taxiroute.puntos_control import PuntoControl, PuntoControlInvalido, guardar_punto


class PruebasFinalizacionPendiente(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.punto = PuntoControl("carrera-1", self.ana.id, self.fecha, self.fecha + timedelta(seconds=30),
                                  "en movimiento", 10_000_000_000, 20_000_000_000, "0.02", "0.05", 2)

    def registro(self):
        with conectar(self.ruta) as conexion:
            return dict(conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone())

    def test_finaliza_ultimo_punto_y_reintento_identico(self):
        guardar_punto(self.ruta, self.punto)
        resumen = finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        self.assertEqual(resumen.importe, Decimal("1.20"))
        self.assertEqual(self.registro()["estado_registro"], "guardado_pendiente")
        self.assertEqual(self.registro()["importe_final"], "1.20")
        self.assertEqual(finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control), resumen)
        nuevo = replace(self.punto, carrera_id="otra")
        with self.assertRaises(sqlite3.IntegrityError):
            guardar_punto(self.ruta, nuevo)

    def test_rechaza_sin_punto_y_captura_distinta_sin_mutar(self):
        with self.assertRaises(PuntoControlInvalido):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        guardar_punto(self.ruta, self.punto)
        for cambio in ({"usuario_id": self.luis.id}, {"revision": 1}, {"parado_ns": 0},
                       {"tarifa_parado": "0.03"}, {"carrera_id": "otra"}):
            with self.subTest(cambio=cambio), self.assertRaises(PuntoControlInvalido):
                finalizar_punto_guardado(self.ruta, replace(self.punto, **cambio), self.punto.ultimo_control)
        with self.assertRaises(PuntoControlInvalido):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control + timedelta(seconds=1))
        self.assertEqual(self.registro()["estado_registro"], "activa")
        self.assertIsNone(self.registro()["importe_final"])

    def test_redondea_solo_al_final(self):
        punto = replace(self.punto, ultimo_control=self.fecha + timedelta(seconds=1),
                        parado_ns=200_000_000, movimiento_ns=20_000_000)
        guardar_punto(self.ruta, punto)
        resumen = finalizar_punto_guardado(self.ruta, punto, punto.ultimo_control)
        self.assertEqual(resumen.importe, Decimal("0.01"))

    def test_fallo_actualizacion_revierte_transicion(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_final BEFORE UPDATE ON carreras_recuperacion
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        self.assertEqual(self.registro()["estado_registro"], "activa")
        self.assertIsNone(self.registro()["fin"])

    def test_dos_finalizaciones_iguales_coinciden_en_un_solo_registro(self):
        guardar_punto(self.ruta, self.punto)
        barrera = Barrier(2)
        def finalizar():
            barrera.wait(timeout=5)
            return finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: finalizar(), range(2)))
        self.assertEqual(resultados[0], resultados[1])
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion").fetchone()[0], 1)
        self.assertEqual(self.registro()["estado_registro"], "guardado_pendiente")

    def test_actualizacion_ignorada_no_comunica_exito(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER ignorar_final BEFORE UPDATE ON carreras_recuperacion
                BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaises(PuntoControlInvalido):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        self.assertEqual(self.registro()["estado_registro"], "activa")

    def test_fechas_con_zona_equivalente_y_sin_zona(self):
        guardar_punto(self.ruta, self.punto)
        with self.assertRaises(PuntoControlInvalido):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control.replace(tzinfo=None))
        otra_zona = timezone(timedelta(hours=2))
        resumen = finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control.astimezone(otra_zona))
        self.assertEqual(resumen.fin, self.punto.ultimo_control)
    def test_carrera_en_revision_no_puede_finalizarse_automaticamente(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='revision_pendiente'")
        with self.assertRaises(PuntoControlInvalido):
            finalizar_punto_guardado(self.ruta, self.punto, self.punto.ultimo_control)
        self.assertEqual(self.registro()["estado_registro"], "revision_pendiente")