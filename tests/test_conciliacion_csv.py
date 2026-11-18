"""Conciliación experimental: bases y CSV temporales, sin cuentas reales."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import ConciliacionCSVError, conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final, resumen_para_historial
from taxiroute.historial import HistorialCSV
from taxiroute.puntos_control import PuntoControl


class PruebasConciliacionCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.base = self.carpeta / "cuentas.sqlite3"
        self.csv = self.carpeta / "historial.csv"
        inicializar_base(self.base)
        ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.ana, self.luis = ana.id, luis.id
        fecha = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.punto = PuntoControl("carrera-1", self.ana, fecha, fecha + timedelta(seconds=30),
                                  "en movimiento", 10_000_000_000, 20_000_000_000, "0.02", "0.05", 2)
        self.pendiente = preparar_punto_final(self.base, self.punto, self.punto.ultimo_control)

    def conciliar(self, usuario=None):
        return conciliar_pendiente_csv(self.base, self.csv, usuario or self.ana, self.punto.carrera_id)

    def estado(self):
        with conectar(self.base) as conexion:
            return conexion.execute("SELECT estado_registro FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()[0]

    def test_exito_y_reintento_verifican_una_sola_fila(self):
        self.assertFalse(self.conciliar().ya_guardada)
        self.assertEqual(self.estado(), "guardada")
        self.assertTrue(self.conciliar().ya_guardada)
        filas = HistorialCSV(self.csv).consultar()
        self.assertEqual(len(filas), 1)
        self.assertEqual((filas[0]["identificador"], filas[0]["importe"]), ("carrera-1", "1.20"))

    def test_cuenta_ajena_no_escribe_historial(self):
        with self.assertRaises(ConciliacionCSVError):
            self.conciliar(self.luis)
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")

    def test_interrupcion_despues_de_csv_reintenta_sin_duplicar(self):
        from taxiroute.conciliacion_csv import _verificar_csv
        def interrupcion(historial, resumen):
            _verificar_csv(historial, resumen)
            raise OSError("fallo simulado antes de confirmar")
        with patch("taxiroute.conciliacion_csv._verificar_csv", side_effect=interrupcion):
            with self.assertRaises(OSError):
                self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)
        self.assertFalse(self.conciliar().ya_guardada)
        self.assertEqual(self.estado(), "guardada")
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_dos_conciliaciones_simultaneas_no_duplican(self):
        barrera = Barrier(2)
        def trabajo():
            barrera.wait(timeout=5)
            return self.conciliar()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: trabajo(), range(2)))
        self.assertEqual(sum(not resultado.ya_guardada for resultado in resultados), 1)
        self.assertEqual(self.estado(), "guardada")
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_otro_proceso_no_accede_entre_verificacion_y_commit(self):
        from taxiroute.conciliacion_csv import _verificar_csv
        def comprobar_bloqueo(historial, resumen):
            _verificar_csv(historial, resumen)
            programa = (
                "import sys\n"
                "from taxiroute.bloqueo import bloqueo_historial, HistorialOcupado\n"
                "try:\n"
                " with bloqueo_historial(sys.argv[1], espera=0.2): pass\n"
                "except HistorialOcupado:\n"
                " sys.exit(0)\n"
                "sys.exit(1)\n"
            )
            proceso = subprocess.run([sys.executable, "-c", programa, str(self.csv)],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(proceso.returncode, 0, proceso.stderr)
        with patch("taxiroute.conciliacion_csv._verificar_csv", side_effect=comprobar_bloqueo):
            self.conciliar()
        self.assertEqual(self.estado(), "guardada")

    def test_conflicto_csv_no_confirma_sqlite(self):
        conflicto = replace(resumen_para_historial(self.pendiente), importe=self.pendiente.importe + 1)
        HistorialCSV(self.csv).guardar(conflicto)
        with self.assertRaises(ValueError):
            self.conciliar()
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertEqual(HistorialCSV(self.csv).consultar()[0]["importe"], "2.20")

    def test_fallo_de_update_y_update_ignorado_no_dan_exito(self):
        for comando in ("RAISE(ABORT, 'fallo simulado')", "RAISE(IGNORE)"):
            with self.subTest(comando=comando):
                with conectar(self.base) as conexion:
                    conexion.execute(f"CREATE TRIGGER falla_guardada BEFORE UPDATE ON carreras_recuperacion BEGIN SELECT {comando}; END")
                with self.assertRaises((sqlite3.IntegrityError, ConciliacionCSVError)):
                    self.conciliar()
                self.assertEqual(self.estado(), "guardado_pendiente")
                with conectar(self.base) as conexion:
                    conexion.execute("DROP TRIGGER falla_guardada")
        self.assertFalse(self.conciliar().ya_guardada)
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_guardada_sin_csv_no_recrea_archivo(self):
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada'")
        with self.assertRaises(ConciliacionCSVError):
            self.conciliar()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardada")

    def test_csv_incompleto_y_base_ausente_no_reparan_archivos(self):
        self.csv.write_text("incompleto", encoding="utf-8")
        original = self.csv.read_bytes()
        with self.assertRaises(ValueError):
            self.conciliar()
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(self.estado(), "guardado_pendiente")
        inexistente = self.carpeta / "ausente.sqlite3"
        with self.assertRaises(ConciliacionCSVError):
            conciliar_pendiente_csv(inexistente, self.csv, self.ana, self.punto.carrera_id)
        self.assertFalse(inexistente.exists())

    def test_resumen_corrupto_no_escribe_csv(self):
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET importe_final='999.00'")
        with self.assertRaises(ConciliacionCSVError):
            self.conciliar()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.estado(), "guardado_pendiente")
