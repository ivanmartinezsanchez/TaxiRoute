"""Preparación transaccional del punto final; no usa cuentas reales ni CSV."""
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
from taxiroute.finalizacion_pendiente import preparar_punto_final, preparar_punto_final_en_transaccion
from taxiroute.puntos_control import PuntoControl, PuntoControlInvalido, guardar_punto, leer_punto


class PruebasPreparacionFinal(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        self.ana, self.luis = ana.id, luis.id
        fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.inicial = PuntoControl("carrera-1", self.ana, fecha, fecha + timedelta(seconds=5),
                                    "parado", 5_000_000_000, 0, "0.02", "0.05", 1)
        self.final = replace(self.inicial, ultimo_control=fecha + timedelta(seconds=10),
                             parado_ns=10_000_000_000, revision=2)

    def fila(self):
        with conectar(self.ruta) as conexion:
            dato = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id='carrera-1'").fetchone()
            return dict(dato) if dato else None

    def test_sin_previo_y_reintento_identico(self):
        resumen = preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
        self.assertEqual(resumen.importe.as_tuple().exponent, -2)
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")
        self.assertEqual(preparar_punto_final(self.ruta, self.final, self.final.ultimo_control), resumen)

    def test_punto_identico_o_mas_reciente_puede_prepararse(self):
        guardar_punto(self.ruta, self.inicial)
        preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
        self.assertEqual((self.fila()["parado_ns"], self.fila()["revision"]), (10_000_000_000, 2))
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")
        otra = replace(self.inicial, carrera_id="carrera-2", usuario_id=self.luis)
        guardar_punto(self.ruta, otra)
        preparar_punto_final(self.ruta, otra, otra.ultimo_control)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM carreras_recuperacion WHERE estado_registro='guardado_pendiente'").fetchone()[0], 2)

    def test_fallo_segundo_paso_revierte_insercion_y_actualizacion(self):
        for previo in (False, True):
            with self.subTest(previo=previo):
                if previo:
                    guardar_punto(self.ruta, self.inicial)
                with conectar(self.ruta) as conexion:
                    conexion.execute("""CREATE TRIGGER falla_pendiente BEFORE UPDATE ON carreras_recuperacion
                        WHEN NEW.estado_registro='guardado_pendiente'
                        BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
                if previo:
                    self.assertEqual(leer_punto(self.ruta, self.ana, self.inicial.carrera_id), self.inicial)
                else:
                    self.assertIsNone(self.fila())
                with conectar(self.ruta) as conexion:
                    conexion.execute("DROP TRIGGER falla_pendiente")
                    if previo:
                        conexion.execute("DELETE FROM carreras_recuperacion")

    def test_escritura_ignorada_no_puede_dar_exito(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER ignorar_insercion BEFORE INSERT ON carreras_recuperacion
                BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaises(PuntoControlInvalido):
            preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
        self.assertIsNone(self.fila())

    def test_actualizacion_del_punto_ignorada_no_da_exito(self):
        guardar_punto(self.ruta, self.inicial)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER ignorar_punto BEFORE UPDATE ON carreras_recuperacion
                WHEN NEW.estado_registro='activa' BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaises(PuntoControlInvalido):
            preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
        self.assertEqual(leer_punto(self.ruta, self.ana, self.inicial.carrera_id), self.inicial)

    def test_finalizaciones_identicas_concurrentes_son_reintentos(self):
        barrera = Barrier(2)
        def cerrar(_):
            barrera.wait(timeout=5)
            return preparar_punto_final(self.ruta, self.final, self.final.ultimo_control)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(cerrar, range(2)))
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")
    def test_captura_antigua_o_otra_cuenta_no_modifican_datos(self):
        guardar_punto(self.ruta, self.final)
        for punto in (self.inicial, replace(self.final, usuario_id=self.luis),
                      replace(self.final, tarifa_parado="0.03")):
            with self.subTest(punto=punto), self.assertRaises(PuntoControlInvalido):
                preparar_punto_final(self.ruta, punto, punto.ultimo_control)
        self.assertEqual(leer_punto(self.ruta, self.ana, self.final.carrera_id), self.final)

    def test_revision_pendiente_no_se_finaliza_sin_revision(self):
        guardar_punto(self.ruta, self.inicial)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='revision_pendiente'")
        with self.assertRaises(PuntoControlInvalido):
            preparar_punto_final(self.ruta, self.inicial, self.inicial.ultimo_control)
        self.assertEqual(self.fila()["estado_registro"], "revision_pendiente")

    def test_no_confirma_transaccion_del_llamador(self):
        with conectar(self.ruta) as conexion:
            with self.assertRaises(ValueError):
                preparar_punto_final_en_transaccion(conexion, self.final, self.final.ultimo_control)
            conexion.execute("BEGIN IMMEDIATE")
            preparar_punto_final_en_transaccion(conexion, self.final, self.final.ultimo_control)
            self.assertTrue(conexion.in_transaction)
            conexion.rollback()
        self.assertIsNone(self.fila())

    def test_error_restaura_el_punto_aunque_llamador_confirme_otros_cambios(self):
        guardar_punto(self.ruta, self.inicial)
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            conexion.execute("UPDATE usuarios SET nombre_visible=? WHERE id=?", ("Ana actualizada", self.ana))
            with self.assertRaises(PuntoControlInvalido):
                preparar_punto_final_en_transaccion(
                    conexion, self.final, self.final.ultimo_control + timedelta(seconds=1))
            self.assertTrue(conexion.in_transaction)
            conexion.commit()
        self.assertEqual(leer_punto(self.ruta, self.ana, self.inicial.carrera_id), self.inicial)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT nombre_visible FROM usuarios WHERE id=?",
                                             (self.ana,)).fetchone()[0], "Ana actualizada")
    def test_finalizaciones_distintas_concurrentes_no_mezclan_datos(self):
        barrera = Barrier(2)
        def cerrar(punto):
            barrera.wait(timeout=5)
            try:
                return preparar_punto_final(self.ruta, punto, punto.ultimo_control)
            except PuntoControlInvalido:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(cerrar, [self.inicial, self.final]))
        self.assertEqual(sum(r is not None for r in resultados), 1)
        elegido = next(r for r in resultados if r is not None)
        self.assertEqual(self.fila()["revision"], elegido.punto.revision)
        self.assertEqual(self.fila()["estado_registro"], "guardado_pendiente")