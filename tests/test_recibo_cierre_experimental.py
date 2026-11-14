from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from uuid import uuid4
import sqlite3
import unittest

from taxiroute.recibo_cierre_experimental import TABLA, ReciboCierreError, consultar, preparar
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.puntos_control import PuntoControl, guardar_punto


class PruebasReciboCierre(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.anterior = PuntoControl("carrera-ficticia", self.ana.id, fecha, fecha + timedelta(seconds=10),
                                     "parado", 10_000_000_000, 0, "0.02", "0.05", 1)
        self.final = replace(self.anterior, ultimo_control=fecha + timedelta(seconds=20), parado_ns=20_000_000_000, revision=2)
        guardar_punto(self.ruta, self.anterior)
        with conectar(self.ruta) as c:
            c.execute(TABLA)
        self.operacion = str(uuid4())

    def llamar(self, *, operacion=None, usuario=None, punto=None, revision=1):
        with conectar(self.ruta) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                resultado = preparar(c, id_operacion=operacion or self.operacion,
                    usuario_id=usuario or self.ana.id, revision=revision, punto=punto or self.final)
                c.commit()
                return resultado
            except BaseException:
                c.rollback()
                raise

    def test_reintento_identico_recupera_el_mismo_punto_pendiente(self):
        primero = self.llamar()
        segundo = self.llamar()
        self.assertEqual(primero[0], segundo[0])
        self.assertEqual((primero[1], primero[2], segundo[1], segundo[2]),
                         ("guardado_pendiente", False, "guardado_pendiente", True))
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre_ensayo").fetchone()[0], 1)

    def test_id_reutilizado_por_otra_cuenta_o_captura_se_rechaza(self):
        self.llamar()
        with self.assertRaises(ReciboCierreError):
            self.llamar(usuario=self.luis.id, punto=replace(self.final, usuario_id=self.luis.id))
        with self.assertRaises(ReciboCierreError):
            self.llamar(punto=replace(self.final, parado_ns=19_000_000_000))

    def test_error_al_insertar_revierte_el_punto_final(self):
        with conectar(self.ruta) as c:
            c.execute("CREATE TRIGGER fallo_recibo BEFORE INSERT ON recibos_cierre_ensayo BEGIN SELECT RAISE(ABORT, 'ficticio'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.llamar()
        with conectar(self.ruta) as c:
            fila = c.execute("SELECT estado_registro, revision FROM carreras_recuperacion").fetchone()
            self.assertEqual(tuple(fila), ("activa", 1))
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre_ensayo").fetchone()[0], 0)

    def test_transaccion_revertida_no_confirma_cierre_ni_recibo(self):
        with conectar(self.ruta) as c:
            c.execute("BEGIN IMMEDIATE")
            preparar(c, id_operacion=self.operacion, usuario_id=self.ana.id, revision=1, punto=self.final)
            c.rollback()
            self.assertEqual(c.execute("SELECT estado_registro FROM carreras_recuperacion").fetchone()[0], "activa")
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre_ensayo").fetchone()[0], 0)

    def test_recibo_alterado_durante_guardado_revierte_el_cierre(self):
        with conectar(self.ruta) as c:
            c.execute("""CREATE TRIGGER alterar_recibo AFTER INSERT ON recibos_cierre_ensayo
                BEGIN UPDATE recibos_cierre_ensayo SET punto_json='{}' WHERE id=NEW.id; END""")
        with self.assertRaises(ReciboCierreError):
            self.llamar()
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT estado_registro FROM carreras_recuperacion").fetchone()[0], "activa")
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre_ensayo").fetchone()[0], 0)

    def test_reintentos_simultaneos_producen_un_solo_comprobante(self):
        barrera = Barrier(2)
        def ejecutar():
            barrera.wait(timeout=5)
            return self.llamar()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: ejecutar(), range(2)))
        self.assertEqual(sorted(r[2] for r in resultados), [False, True])
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM recibos_cierre_ensayo").fetchone()[0], 1)

    def test_consulta_al_reabrir_y_rechaza_cuenta_ajena(self):
        resumen, _, _ = self.llamar()
        with conectar(self.ruta) as c:
            c.execute("BEGIN")
            self.assertEqual(consultar(c, id_operacion=self.operacion, usuario_id=self.ana.id, revision=1),
                             (resumen, "guardado_pendiente"))
            self.assertIsNone(consultar(c, id_operacion=str(uuid4()), usuario_id=self.ana.id, revision=1))
            with self.assertRaises(ReciboCierreError):
                consultar(c, id_operacion=self.operacion, usuario_id=self.luis.id, revision=1)
            with self.assertRaises(ReciboCierreError):
                consultar(c, id_operacion=self.operacion, usuario_id=self.ana.id, revision=2)
            c.rollback()
