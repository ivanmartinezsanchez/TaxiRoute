"""Migración no destructiva de historiales ficticios mediante bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import csv
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from taxiroute.carrera import ResumenCarrera
from taxiroute.historial import CAMPOS, HistorialCSV, _fila
from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError
from taxiroute.migracion_historial import MigracionHistorialError, migrar_historial_csv


class PruebasMigracionHistorial(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.csv = carpeta / "origen.csv"
        self.sqlite = carpeta / "historial.sqlite3"
        fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.resumen = ResumenCarrera("carrera-1", fecha, fecha + timedelta(seconds=30),
            Decimal("10"), Decimal("20"), Decimal("0.02"), Decimal("0.05"), Decimal("1.20"))
        self.otro = replace(self.resumen, identificador="carrera-2")
        HistorialCSV(self.csv).guardar(self.resumen)
        HistorialCSV(self.csv).guardar(self.otro)

    def test_migra_sin_atribuir_ni_cambiar_csv_y_reintento_es_idempotente(self):
        original = self.csv.read_bytes()
        resultado = migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual((resultado.filas_origen, resultado.insertadas, resultado.ya_existentes), (2, 2, 0))
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(HistorialSQLite(self.sqlite).consultar(), [_fila(self.resumen), _fila(self.otro)])
        self.assertEqual(HistorialSQLite(self.sqlite, usuario_id="ana").consultar(), [])
        segundo = migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual((segundo.filas_origen, segundo.insertadas, segundo.ya_existentes), (2, 0, 2))
        self.assertEqual(self.csv.read_bytes(), original)

    def test_conflicto_con_otro_propietario_revierte_todas_las_filas(self):
        HistorialSQLite(self.sqlite, usuario_id="ana").guardar(self.otro)
        original = self.csv.read_bytes()
        with self.assertRaises(MigracionHistorialError):
            migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(HistorialSQLite(self.sqlite).consultar(), [])
        self.assertEqual(HistorialSQLite(self.sqlite, usuario_id="ana").consultar(), [_fila(self.otro)])

    def test_fallo_sql_revierte_inserciones_y_conserva_csv(self):
        existente = replace(self.resumen, identificador="existente")
        HistorialSQLite(self.sqlite).guardar(existente)
        with closing(sqlite3.connect(self.sqlite)) as conexion:
            conexion.execute("""CREATE TRIGGER falla_segunda BEFORE INSERT ON carreras
                WHEN NEW.identificador='carrera-2' BEGIN SELECT RAISE(ABORT, 'simulado'); END""")
            conexion.commit()
        original = self.csv.read_bytes()
        with self.assertRaises(HistorialSQLiteError):
            migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(HistorialSQLite(self.sqlite).consultar(), [_fila(existente)])

    def test_csv_incoherente_no_crea_base_ni_cambia_origen(self):
        for campo, valor in (("importe", "999.00"), ("duracion_segundos", "30.1"),
                             ("inicio", "fecha-mala"), ("tarifa_parado", "NaN")):
            with self.subTest(campo=campo):
                filas = [_fila(self.resumen), _fila(self.otro)]
                filas[-1][campo] = valor
                with self.csv.open("w", encoding="utf-8", newline="") as archivo:
                    escritor = csv.DictWriter(archivo, fieldnames=CAMPOS, lineterminator="\n")
                    escritor.writeheader()
                    escritor.writerows(filas)
                original = self.csv.read_bytes()
                with self.assertRaises(MigracionHistorialError):
                    migrar_historial_csv(self.csv, self.sqlite)
                self.assertFalse(self.sqlite.exists())
                self.assertEqual(self.csv.read_bytes(), original)

    def test_csv_con_identificador_duplicado_y_distinto_revierte(self):
        primera = _fila(self.resumen)
        segunda = dict(primera)
        segunda["importe"] = "0.20"
        segunda["segundos_parado"] = "10"
        segunda["segundos_movimiento"] = "0"
        segunda["duracion_segundos"] = "10"
        with self.csv.open("w", encoding="utf-8", newline="") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=CAMPOS, lineterminator="\n")
            escritor.writeheader()
            escritor.writerows((primera, segunda))
        original = self.csv.read_bytes()
        with self.assertRaises(MigracionHistorialError):
            migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual(self.csv.read_bytes(), original)
        # SQLite puede crear el archivo vacío, pero no conserva datos migrados.
        with closing(sqlite3.connect(self.sqlite)) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(conexion.execute("SELECT name FROM sqlite_schema WHERE name='carreras'").fetchone())
    def test_origen_faltante_y_destino_identico_no_cambian_csv(self):
        original = self.csv.read_bytes()
        with self.assertRaises(MigracionHistorialError):
            migrar_historial_csv(self.csv, self.csv)
        with self.assertRaises(MigracionHistorialError):
            migrar_historial_csv(self.csv.parent / "ausente.csv", self.sqlite)
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertFalse(self.sqlite.exists())

    def test_csv_incompleto_y_base_ajena_no_se_modifican(self):
        with closing(sqlite3.connect(self.sqlite)) as conexion:
            conexion.execute("CREATE TABLE ajena (texto TEXT)")
            conexion.execute("INSERT INTO ajena VALUES ('conservar')")
            conexion.commit()
        with self.assertRaises(HistorialSQLiteError):
            migrar_historial_csv(self.csv, self.sqlite)
        with closing(sqlite3.connect(self.sqlite)) as conexion:
            self.assertEqual(conexion.execute("SELECT texto FROM ajena").fetchone()[0], "conservar")
        self.csv.write_bytes(self.csv.read_bytes().rstrip(b"\n"))
        original = self.csv.read_bytes()
        with self.assertRaises(MigracionHistorialError):
            migrar_historial_csv(self.csv, self.sqlite)
        self.assertEqual(self.csv.read_bytes(), original)

    def test_dos_migraciones_concurrentes_no_duplican_filas(self):
        barrera = Barrier(2)
        def migrar(_):
            barrera.wait(timeout=5)
            return migrar_historial_csv(self.csv, self.sqlite)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(migrar, range(2)))
        self.assertEqual(sorted(x.insertadas for x in resultados), [0, 2])
        self.assertEqual(len(HistorialSQLite(self.sqlite).consultar()), 2)