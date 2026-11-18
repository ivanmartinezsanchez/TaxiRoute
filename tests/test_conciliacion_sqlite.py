"""Conciliación entre bases independientes de prueba."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import subprocess
import sys
from threading import Barrier
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final, resumen_para_historial
from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError
from taxiroute.puntos_control import PuntoControl


class PruebasConciliacionSQLite(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        self.destino = Path(temporal.name) / "historial.sqlite3"
        inicializar_base(self.base)
        ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.ana, self.luis = ana.id, luis.id
        fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.punto = PuntoControl("carrera-1", self.ana, fecha, fecha + timedelta(seconds=30),
                                  "en movimiento", 10_000_000_000, 20_000_000_000, "0.02", "0.05", 2)
        self.resumen = preparar_punto_final(self.base, self.punto, self.punto.ultimo_control)
        self.historial = HistorialSQLite(self.destino, usuario_id=self.ana, exigir_existente=True)
        self.historial.inicializar()

    def conciliar(self, usuario=None):
        return conciliar_pendiente_sqlite(self.base, self.destino, usuario or self.ana, self.punto.carrera_id)

    def estado(self):
        with conectar(self.base) as conexion:
            return conexion.execute("SELECT estado_registro FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()[0]

    def test_exito_reintento_y_aislamiento(self):
        self.assertFalse(self.conciliar())
        self.assertTrue(self.conciliar())
        self.assertEqual(self.estado(), "guardada")
        self.assertEqual([(x["identificador"], x["importe"]) for x in self.historial.consultar()], [("carrera-1", "1.20")])
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.luis, exigir_existente=True).consultar(), [])

    def test_usuario_ajeno_y_destino_ausente_no_confirman(self):
        with self.assertRaises(ValueError):
            self.conciliar(self.luis)
        ausente = self.destino.parent / "no-existe.sqlite3"
        with self.assertRaises(HistorialSQLiteError):
            conciliar_pendiente_sqlite(self.base, ausente, self.ana, self.punto.carrera_id)
        self.assertFalse(ausente.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_corte_tras_historial_reintenta_sin_duplicar(self):
        with patch("taxiroute.conciliacion_sqlite._confirmar", side_effect=OSError("corte simulado")):
            with self.assertRaises(OSError):
                self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(len(self.historial.consultar()), 1)
        self.assertFalse(self.conciliar())
        self.assertEqual(len(self.historial.consultar()), 1)
        self.assertEqual(self.estado(), "guardada")

    def test_dos_conciliaciones_concurrentes_solo_guardan_una_vez(self):
        barrera = Barrier(2)
        def trabajo():
            barrera.wait(timeout=5)
            return self.conciliar()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: trabajo(), range(2)))
        self.assertEqual(sorted(resultados), [False, True])
        self.assertEqual(len(self.historial.consultar()), 1)
        self.assertEqual(self.estado(), "guardada")

    def test_cambio_en_historial_entre_las_fases_impide_confirmacion(self):
        from taxiroute.conciliacion_sqlite import _preparar_historial
        def preparar_y_cambiar(*args):
            _preparar_historial(*args)
            with closing(sqlite3.connect(self.destino)) as conexion:
                conexion.execute("UPDATE carreras SET importe='9.99'")
                conexion.commit()
        with patch("taxiroute.conciliacion_sqlite._preparar_historial", side_effect=preparar_y_cambiar):
            with self.assertRaises(ValueError):
                self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(self.historial.consultar()[0]["importe"], "9.99")

    def test_otro_proceso_no_puede_escribir_historial_antes_de_confirmar(self):
        from taxiroute.conciliacion_sqlite import _comprobar_fila
        llamadas = [0]
        def comprobar_y_probar(conexion, esperada, usuario_id, *, permitir_insertar):
            _comprobar_fila(conexion, esperada, usuario_id, permitir_insertar=permitir_insertar)
            if not permitir_insertar:
                llamadas[0] += 1
            if not permitir_insertar and llamadas[0] == 2:
                programa = (
                    "import sqlite3, sys\n"
                    "c = sqlite3.connect(sys.argv[1], timeout=0.2, isolation_level=None)\n"
                    "try:\n"
                    " c.execute('BEGIN IMMEDIATE')\n"
                    "except sqlite3.OperationalError:\n"
                    " sys.exit(0)\n"
                    "sys.exit(1)\n"
                )
                proceso = subprocess.run([sys.executable, "-c", programa, str(self.destino)],
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(proceso.returncode, 0, proceso.stderr)
        with patch("taxiroute.conciliacion_sqlite._comprobar_fila", side_effect=comprobar_y_probar):
            self.conciliar()
        self.assertEqual(llamadas[0], 2)
        self.assertEqual(self.estado(), "guardada")

    def test_guardada_sin_historial_no_recrea_la_fila(self):
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada'")
        with self.assertRaises(ValueError):
            self.conciliar()
        self.assertEqual(self.historial.consultar(), [])

    def test_conflicto_de_propietario_no_confirma(self):
        ajeno = HistorialSQLite(self.destino, usuario_id=self.luis, exigir_existente=True)
        ajeno.guardar(resumen_para_historial(self.resumen))
        with self.assertRaises(ValueError):
            self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(self.historial.consultar(), [])

    def test_fallo_al_confirmar_conserva_pendiente_y_permita_reintento(self):
        with conectar(self.base) as conexion:
            conexion.execute("CREATE TRIGGER falla BEFORE UPDATE ON carreras_recuperacion BEGIN SELECT RAISE(ABORT, 'simulado'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(len(self.historial.consultar()), 1)
        with conectar(self.base) as conexion:
            conexion.execute("DROP TRIGGER falla")
        self.assertFalse(self.conciliar())
        self.assertEqual(len(self.historial.consultar()), 1)

    def test_base_de_historial_no_puede_ser_base_de_cuentas(self):
        with self.assertRaises(ValueError):
            conciliar_pendiente_sqlite(self.base, self.base, self.ana, self.punto.carrera_id)
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_conflicto_de_importe_no_confirma(self):
        resumen = resumen_para_historial(self.resumen)
        self.historial.guardar(replace(resumen, importe=resumen.importe + 1))
        with self.assertRaises(ValueError):
            self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(self.historial.consultar()[0]["importe"], "2.20")
