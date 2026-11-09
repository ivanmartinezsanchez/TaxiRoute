"""Contrato del historial SQLite con archivos y carreras temporales."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from taxiroute.carrera import ResumenCarrera
from taxiroute.historial import CAMPOS, _fila
from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError
from taxiroute.servicio import ServicioCarreras


class PruebasHistorialSQLite(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "datos" / "historial.sqlite3"
        self.historial = HistorialSQLite(self.ruta)
        inicio = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.resumen = ResumenCarrera(
            "carrera-1", inicio, inicio + timedelta(seconds=30),
            Decimal("10.000000001"), Decimal("19.999999999"),
            Decimal("0.02"), Decimal("0.05"), Decimal("1.20"),
        )

    def test_ausente_no_crea_base_y_lectura_conserva_decimales(self):
        self.assertEqual(self.historial.consultar(), [])
        self.assertFalse(self.ruta.exists())
        self.historial.guardar(self.resumen)
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen)])
        self.assertEqual(set(self.historial.consultar()[0]), set(CAMPOS))
        self.assertEqual(HistorialSQLite(self.ruta).consultar(), [_fila(self.resumen)])

    def test_reintento_identico_y_conflicto_sin_alterar_datos(self):
        self.historial.guardar(self.resumen)
        self.historial.guardar(self.resumen)
        with self.assertRaises(ValueError):
            self.historial.guardar(replace(self.resumen, importe=Decimal("9.99")))
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen)])

    def test_varios_registros_conservan_orden_de_insercion(self):
        otro = replace(self.resumen, identificador="carrera-2", importe=Decimal("2.25"))
        self.historial.guardar(self.resumen)
        self.historial.guardar(otro)
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen), _fila(otro)])

    def test_dos_guardados_concurrentes_no_duplican(self):
        barrera = Barrier(2)
        def guardar():
            barrera.wait(timeout=5)
            HistorialSQLite(self.ruta).guardar(self.resumen)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            list(ejecutor.map(lambda _: guardar(), range(2)))
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen)])

    def test_no_adopta_base_ajena_ni_cambia_sus_datos(self):
        self.ruta.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            conexion.execute("CREATE TABLE ajena (dato TEXT)")
            conexion.execute("INSERT INTO ajena VALUES ('conservar')")
            conexion.commit()
        with self.assertRaises(HistorialSQLiteError):
            self.historial.guardar(self.resumen)
        with self.assertRaises(HistorialSQLiteError):
            self.historial.consultar()
        with closing(sqlite3.connect(self.ruta)) as conexion:
            self.assertEqual(conexion.execute("SELECT dato FROM ajena").fetchone()[0], "conservar")
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_fallo_guardado_no_borra_registro_existente(self):
        self.historial.guardar(self.resumen)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            conexion.execute("""CREATE TRIGGER falla_carrera BEFORE INSERT ON carreras
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
            conexion.commit()
        with self.assertRaises(HistorialSQLiteError):
            self.historial.guardar(replace(self.resumen, identificador="otra"))
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen)])

    def test_consultas_aisladas_y_no_atribuidas(self):
        ana = HistorialSQLite(self.ruta, usuario_id="ana")
        luis = HistorialSQLite(self.ruta, usuario_id="luis")
        anonima = HistorialSQLite(self.ruta, usuario_id=None)
        carrera_ana = replace(self.resumen, identificador="carrera-ana")
        carrera_luis = replace(self.resumen, identificador="carrera-luis")
        carrera_antigua = replace(self.resumen, identificador="carrera-antigua")
        ana.guardar(carrera_ana)
        luis.guardar(carrera_luis)
        anonima.guardar(carrera_antigua)
        self.assertEqual(ana.consultar(), [_fila(carrera_ana)])
        self.assertEqual(luis.consultar(), [_fila(carrera_luis)])
        self.assertEqual(anonima.consultar(), [_fila(carrera_antigua)])
        self.assertEqual(HistorialSQLite(self.ruta, usuario_id="otro").consultar(), [])

    def test_mismo_identificador_no_se_puede_reasignar(self):
        ana = HistorialSQLite(self.ruta, usuario_id="ana")
        luis = HistorialSQLite(self.ruta, usuario_id="luis")
        ana.guardar(self.resumen)
        with self.assertRaises(ValueError):
            luis.guardar(self.resumen)
        with self.assertRaises(ValueError):
            self.historial.guardar(self.resumen)
        self.assertEqual(ana.consultar(), [_fila(self.resumen)])
        self.assertEqual(luis.consultar(), [])
        self.assertEqual(self.historial.consultar(), [])

    def test_dos_propietarios_no_pueden_guardar_misma_carrera_a_la_vez(self):
        barrera = Barrier(2)
        def guardar(usuario):
            barrera.wait(timeout=5)
            try:
                HistorialSQLite(self.ruta, usuario_id=usuario).guardar(self.resumen)
                return (usuario, "guardada")
            except ValueError:
                return (usuario, "rechazada")
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(guardar, ("ana", "luis")))
        self.assertEqual(sorted(estado for _, estado in resultados), ["guardada", "rechazada"])
        ganador = next(usuario for usuario, estado in resultados if estado == "guardada")
        perdedor = next(usuario for usuario, estado in resultados if estado == "rechazada")
        self.assertEqual(HistorialSQLite(self.ruta, usuario_id=ganador).consultar(), [_fila(self.resumen)])
        self.assertEqual(HistorialSQLite(self.ruta, usuario_id=perdedor).consultar(), [])
    def test_no_se_reasignan_carreras_historicas(self):
        self.historial.guardar(self.resumen)
        ana = HistorialSQLite(self.ruta, usuario_id="ana")
        with self.assertRaises(ValueError):
            ana.guardar(self.resumen)
        self.assertEqual(ana.consultar(), [])
        self.assertEqual(self.historial.consultar(), [_fila(self.resumen)])

    def test_identificador_de_propietario_no_valido_se_rechaza(self):
        for valor in ("", "   ", " ana", "ana ", 1, True):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                HistorialSQLite(self.ruta, usuario_id=valor)
        self.assertFalse(self.ruta.exists())

    def test_base_v1_no_se_modifica_sin_migracion(self):
        self.historial.guardar(self.resumen)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            conexion.execute("PRAGMA user_version=1")
        with self.assertRaises(HistorialSQLiteError):
            self.historial.guardar(replace(self.resumen, identificador="otra"))
        with closing(sqlite3.connect(self.ruta)) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras").fetchone()[0], 1)
    def test_modo_estricto_exige_inicializacion_explicita(self):
        estricto = HistorialSQLite(self.ruta, usuario_id="ana", exigir_existente=True)
        with self.assertRaises(HistorialSQLiteError):
            estricto.consultar()
        with self.assertRaises(HistorialSQLiteError):
            estricto.guardar(self.resumen)
        self.assertFalse(self.ruta.exists())
        estricto.inicializar()
        self.assertEqual(estricto.consultar(), [])
        estricto.guardar(self.resumen)
        estricto.inicializar()
        self.assertEqual(estricto.consultar(), [_fila(self.resumen)])
        self.assertEqual(HistorialSQLite(self.ruta, usuario_id="luis", exigir_existente=True).consultar(), [])

    def test_archivo_perdido_no_se_recrea_ni_se_interpreta_vacio(self):
        estricto = HistorialSQLite(self.ruta, usuario_id="ana", exigir_existente=True)
        estricto.inicializar()
        estricto.guardar(self.resumen)
        apartada = self.ruta.with_name("copia-temporal.sqlite3")
        self.ruta.rename(apartada)
        with self.assertRaises(HistorialSQLiteError):
            estricto.consultar()
        with self.assertRaises(HistorialSQLiteError):
            estricto.guardar(replace(self.resumen, identificador="otra"))
        self.assertFalse(self.ruta.exists())
        apartada.rename(self.ruta)
        self.assertEqual(estricto.consultar(), [_fila(self.resumen)])

    def test_archivo_vacio_no_se_adopta_en_modo_estricto(self):
        self.ruta.parent.mkdir(parents=True)
        self.ruta.touch()
        estricto = HistorialSQLite(self.ruta, exigir_existente=True)
        with self.assertRaises(HistorialSQLiteError):
            estricto.consultar()
        with self.assertRaises(HistorialSQLiteError):
            estricto.guardar(self.resumen)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            self.assertEqual(conexion.execute("PRAGMA user_version").fetchone()[0], 0)
        estricto.inicializar()
        self.assertEqual(estricto.consultar(), [])

    def test_inicializacion_no_adopta_base_ajena(self):
        self.ruta.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.ruta)) as conexion:
            conexion.execute("CREATE TABLE ajena (dato TEXT)")
            conexion.execute("INSERT INTO ajena VALUES ('conservar')")
            conexion.commit()
        estricto = HistorialSQLite(self.ruta, exigir_existente=True)
        with self.assertRaises(HistorialSQLiteError):
            estricto.inicializar()
        with closing(sqlite3.connect(self.ruta)) as conexion:
            self.assertEqual(conexion.execute("SELECT dato FROM ajena").fetchone()[0], "conservar")

    def test_exigir_existente_requiere_booleano(self):
        for valor in (None, 0, 1, "si"):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                HistorialSQLite(self.ruta, exigir_existente=valor)
    def test_interfaz_servicio_carreras(self):
        servicio = ServicioCarreras(historial=self.historial)
        servicio.ejecutar("iniciar")
        mensaje, _ = servicio.ejecutar("finalizar")
        self.assertIn("Carrera guardada", mensaje)
        filas, error = servicio.consultar_historial()
        self.assertIsNone(error)
        self.assertEqual(len(filas), 1)