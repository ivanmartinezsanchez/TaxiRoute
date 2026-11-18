"""Puntos de control probados únicamente con cuentas y bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.puntos_control import PuntoControl, PuntoControlInvalido, guardar_punto, leer_punto


class PruebasPuntosControl(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "prueba.sqlite3"
        inicializar_base(self.ruta)
        ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.ana, self.luis = ana.id, luis.id
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.punto = PuntoControl("carrera-1", self.ana, self.fecha, self.fecha, "parado",
                                  0, 0, "0.02", "0.05", 0)

    def test_guardar_actualizar_y_leer_por_usuario(self):
        guardar_punto(self.ruta, self.punto)
        nuevo = replace(self.punto, ultimo_control=self.fecha + timedelta(seconds=5),
                        parado_ns=5_000_000_000)
        guardar_punto(self.ruta, nuevo)
        self.assertEqual(leer_punto(self.ruta, self.ana, "carrera-1"), nuevo)
        self.assertIsNone(leer_punto(self.ruta, self.luis, "carrera-1"))
        self.assertIsNone(leer_punto(self.ruta, self.ana, "no-existe"))

    def test_rechaza_antiguos_campos_inmutables_y_otra_cuenta(self):
        nuevo = replace(self.punto, ultimo_control=self.fecha + timedelta(seconds=5),
                        parado_ns=5_000_000_000, revision=2)
        guardar_punto(self.ruta, nuevo)
        candidatos = [self.punto, replace(nuevo, usuario_id=self.luis, ultimo_control=self.fecha + timedelta(seconds=6)),
                      replace(nuevo, tarifa_parado="0.03", ultimo_control=self.fecha + timedelta(seconds=6)),
                      replace(nuevo, parado_ns=0, ultimo_control=self.fecha + timedelta(seconds=6)),
                      replace(nuevo, revision=1, ultimo_control=self.fecha + timedelta(seconds=6)),
                      replace(nuevo, estado_vehiculo="en movimiento", ultimo_control=self.fecha + timedelta(seconds=6))]
        for candidato in candidatos:
            with self.subTest(candidato=candidato), self.assertRaises(PuntoControlInvalido):
                guardar_punto(self.ruta, candidato)
        self.assertEqual(leer_punto(self.ruta, self.ana, "carrera-1"), nuevo)

    def test_validacion_datos_y_cuenta_inexistente(self):
        candidatos = [replace(self.punto, parado_ns=-1), replace(self.punto, revision=True),
                      replace(self.punto, inicio=self.fecha.replace(tzinfo=None)),
                      replace(self.punto, ultimo_control=self.fecha - timedelta(seconds=1)),
                      replace(self.punto, tarifa_parado="NaN"), replace(self.punto, estado_vehiculo="volando")]
        for candidato in candidatos:
            with self.subTest(candidato=candidato), self.assertRaises(PuntoControlInvalido):
                guardar_punto(self.ruta, candidato)
        with self.assertRaises(sqlite3.IntegrityError):
            guardar_punto(self.ruta, replace(self.punto, usuario_id="inexistente"))
        self.assertIsNone(leer_punto(self.ruta, self.ana, "carrera-1"))

    def test_fallo_de_escritura_conserva_punto_anterior(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_control BEFORE UPDATE ON carreras_recuperacion
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        nuevo = replace(self.punto, ultimo_control=self.fecha + timedelta(seconds=5), parado_ns=5_000_000_000)
        with self.assertRaises(sqlite3.IntegrityError):
            guardar_punto(self.ruta, nuevo)
        self.assertEqual(leer_punto(self.ruta, self.ana, "carrera-1"), self.punto)

    def test_dos_escrituras_del_mismo_punto_solo_una_avanza(self):
        guardar_punto(self.ruta, self.punto)
        nuevo = replace(self.punto, ultimo_control=self.fecha + timedelta(seconds=5), parado_ns=5_000_000_000)
        barrera = Barrier(2)
        def guardar():
            barrera.wait(timeout=5)
            try:
                guardar_punto(self.ruta, nuevo)
                return "guardado"
            except PuntoControlInvalido:
                return "antiguo"
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: guardar(), range(2)))
        self.assertCountEqual(resultados, ["guardado", "antiguo"])
        self.assertEqual(leer_punto(self.ruta, self.ana, "carrera-1"), nuevo)

    def test_datos_guardados_invalidos_no_se_devuelven_como_validos(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET ultimo_control='fecha-invalida'")
        with self.assertRaises(PuntoControlInvalido):
            leer_punto(self.ruta, self.ana, "carrera-1")