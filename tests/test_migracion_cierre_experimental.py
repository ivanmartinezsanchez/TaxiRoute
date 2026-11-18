"""Pruebas de la migración interna con bases de cuentas ficticias."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch
import sqlite3

from taxiroute.migracion_cierre_experimental import MigracionCierreError, migrar_en_transaccion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, crear_flota_con_recuperacion
from taxiroute.cuentas import crear_cuenta
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.sesiones import ServicioSesiones


class PruebasMigracionComponente(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="taxi-migra-ensayo-")
        self.addCleanup(temp.cleanup)
        self.ruta = Path(temp.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.usuario = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.assertIsNotNone(ServicioSesiones(self.ruta).iniciar_conductor("Ana", "ConductorPrueba123!"))
        instante = datetime.now(timezone.utc)
        punto = PuntoControl("carrera-ficticia", self.usuario.id, instante, instante + timedelta(seconds=10),
                             "parado", 10_000_000_000, 0, "0.02", "0.05", 1)
        guardar_punto(self.ruta, punto)
        with conectar(self.ruta) as c:
            c.execute("INSERT INTO vinculo_historial(id, formato, identificador, estado) VALUES (1, 'sqlite', ?, 'listo')", (str(uuid4()),))

    def migrar(self):
        with conectar(self.ruta) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                resultado = migrar_en_transaccion(c)
                c.commit()
                return resultado
            except BaseException:
                c.rollback()
                raise

    def estado(self):
        with conectar(self.ruta) as c:
            return tuple(tuple(tuple(f) for f in c.execute(f"SELECT * FROM {tabla}")) for tabla in ("usuarios", "sesiones", "carreras_recuperacion", "vinculo_historial"))

    def test_conserva_datos_y_repetir_no_modifica(self):
        antes = self.estado()
        self.assertTrue(self.migrar())
        self.assertFalse(self.migrar())
        self.assertEqual(self.estado(), antes)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 15)
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre").fetchone()[0], 0)

    def test_rollback_exterior_devuelve_todo_a_version_anterior(self):
        with conectar(self.ruta) as c:
            c.execute("BEGIN IMMEDIATE")
            self.assertTrue(migrar_en_transaccion(c))
            c.rollback()
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 14)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='recibos_cierre'").fetchone())

    def test_error_sql_no_deja_cambios_en_la_transaccion_exterior(self):
        with conectar(self.ruta) as c:
            c.execute("BEGIN IMMEDIATE")
            with patch("taxiroute.migracion_cierre_experimental.TABLA", "CREATE TABLE ("):
                with self.assertRaises(sqlite3.OperationalError):
                    migrar_en_transaccion(c)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 14)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='recibos_cierre'").fetchone())
            c.execute("UPDATE usuarios SET nombre_visible='Conservado' WHERE id=?", (self.usuario.id,))
            c.commit()
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT nombre_visible FROM usuarios").fetchone()[0], "Conservado")

    def test_tabla_ajena_se_conserva_sin_adoptar(self):
        with conectar(self.ruta) as c:
            c.execute("CREATE TABLE recibos_cierre(dato TEXT)")
            c.execute("INSERT INTO recibos_cierre VALUES ('conservar')")
        with self.assertRaises(MigracionCierreError):
            self.migrar()
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 14)
            self.assertEqual(c.execute("SELECT dato FROM recibos_cierre").fetchone()[0], "conservar")

    def test_ejecuciones_simultaneas_se_serializan(self):
        barrera = Barrier(2)
        def ejecutar():
            barrera.wait(timeout=5)
            return self.migrar()
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(lambda _: ejecutar(), range(2)))
        self.assertEqual(sorted(resultados), [False, True])

    def test_servidor_v14_rechaza_base_migrada_sin_modificar_datos(self):
        self.assertTrue(self.migrar())
        anterior = self.estado()
        with self.assertRaises(ArranqueRecuperacionError):
            crear_flota_con_recuperacion(self.ruta, lambda usuario: None, modo_durable_experimental=True)
        self.assertEqual(self.estado(), anterior)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 15)

    def test_no_acepta_sin_transaccion(self):
        with conectar(self.ruta) as c:
            with self.assertRaises(MigracionCierreError):
                migrar_en_transaccion(c)

    def test_rechaza_referencias_rotas_sin_cambiar_la_version(self):
        with conectar(self.ruta) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("UPDATE sesiones SET usuario_id='usuario-inexistente'")
            c.execute("PRAGMA foreign_keys=ON")
        with self.assertRaises(MigracionCierreError):
            self.migrar()
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 14)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='recibos_cierre'").fetchone())

    def test_version_nueva_con_tabla_alterada_no_se_adopta(self):
        self.assertTrue(self.migrar())
        with conectar(self.ruta) as c:
            c.execute("DROP TABLE recibos_cierre")
            c.execute("CREATE TABLE recibos_cierre(dato TEXT)")
        with self.assertRaises(MigracionCierreError):
            self.migrar()
