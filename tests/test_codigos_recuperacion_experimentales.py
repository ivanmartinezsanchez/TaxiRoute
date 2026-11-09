"""Código interno sin flujo de desbloqueo ni datos reales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.codigos_recuperacion_experimentales import (
    CREAR_TABLA_CODIGOS, consumir_codigo_en_transaccion, generar_codigos_en_transaccion,
)
from taxiroute.cuentas import crear_cuenta


class PruebasCodigosRecuperacion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Prueba",
                                  contrasena="AdminPrueba123!", rol="administrador")
        self.conductor = crear_cuenta(self.ruta, nombre_usuario="Conductor", nombre_visible="Prueba",
                                      contrasena="Conductor123!", rol="conductor")
        with conectar(self.ruta) as conexion:
            conexion.execute(CREAR_TABLA_CODIGOS)

    def generar(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            codigos = generar_codigos_en_transaccion(conexion, self.admin.id)
            conexion.commit()
            return codigos

    def test_solo_guarda_verificadores_y_no_sustituye_lote(self):
        codigos = self.generar()
        self.assertEqual(len(set(codigos)), 5)
        with conectar(self.ruta) as conexion:
            filas = conexion.execute("SELECT verificador FROM codigos_recuperacion").fetchall()
            verificadores = [fila[0] for fila in filas]
            self.assertEqual(len(verificadores), 5)
            self.assertTrue(all(len(x) == 64 and x not in codigos for x in verificadores))
            conexion.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ValueError):
                generar_codigos_en_transaccion(conexion, self.admin.id)
            conexion.rollback()

    def test_consumo_es_unico_y_pertenece_a_la_cuenta(self):
        codigo = self.generar()[0]
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.conductor.id, codigo))
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.admin.id, "no-valido"))
            self.assertTrue(consumir_codigo_en_transaccion(conexion, self.admin.id, codigo))
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.admin.id, codigo))
            conexion.commit()
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.admin.id, codigo))
            conexion.rollback()

    def test_reversion_no_consume_el_codigo(self):
        codigo = self.generar()[0]
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertTrue(consumir_codigo_en_transaccion(conexion, self.admin.id, codigo))
            conexion.rollback()
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertTrue(consumir_codigo_en_transaccion(conexion, self.admin.id, codigo))
            conexion.commit()

    def test_codigo_invalidado_no_se_puede_consumir(self):
        codigos = self.generar()
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            conexion.execute("UPDATE codigos_recuperacion SET invalidado_en=? WHERE usuario_id=?",
                             ("2026-11-09T10:00:00+00:00", self.admin.id))
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.admin.id, codigos[0]))
            conexion.commit()

    def test_lote_revertido_no_es_valido(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            codigos = generar_codigos_en_transaccion(conexion, self.admin.id)
            conexion.rollback()
            conexion.execute("BEGIN IMMEDIATE")
            self.assertFalse(consumir_codigo_en_transaccion(conexion, self.admin.id, codigos[0]))
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)
            conexion.rollback()

    def test_dos_consumos_concurrentes_no_usan_el_codigo_dos_veces(self):
        codigo = self.generar()[0]
        def consumir(_):
            with conectar(self.ruta) as conexion:
                conexion.execute("BEGIN IMMEDIATE")
                resultado = consumir_codigo_en_transaccion(conexion, self.admin.id, codigo)
                conexion.commit()
                return resultado
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            self.assertEqual(sum(ejecutor.map(consumir, (1, 2))), 1)

    def test_solo_administrador_con_transaccion_y_datos_validos(self):
        with conectar(self.ruta) as conexion:
            with self.assertRaises(ValueError):
                generar_codigos_en_transaccion(conexion, self.admin.id)
            with self.assertRaises(ValueError):
                consumir_codigo_en_transaccion(conexion, self.admin.id, "x")
            conexion.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ValueError):
                generar_codigos_en_transaccion(conexion, self.conductor.id)
            for cantidad in (0, 11, True, "5"):
                with self.subTest(cantidad=cantidad), self.assertRaises(ValueError):
                    generar_codigos_en_transaccion(conexion, self.admin.id, cantidad=cantidad)
            with self.assertRaises(ValueError):
                generar_codigos_en_transaccion(conexion, self.admin.id, ahora=datetime(2026, 1, 1))
            conexion.rollback()


if __name__ == "__main__":
    unittest.main()
