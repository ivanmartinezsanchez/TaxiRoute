"""Arranque aislado de flota a partir de una base SQLite temporal."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, crear_flota_con_recuperacion
from taxiroute.base_cuentas import VERSION_ESQUEMA, conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final
from taxiroute.flota import FlotaBloqueada
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.flota import FlotaEnMemoria
from taxiroute.sesiones import ServicioSesiones


class PruebasArranqueRecuperacion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.cuentas = [crear_cuenta(self.ruta, nombre_usuario=nombre, nombre_visible=nombre,
                        contrasena="ConductorPrueba123!") for nombre in ("Ana", "Luis", "Marta", "Pablo", "Sara")]
        fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.punto = PuntoControl("carrera-1", self.cuentas[0].id, fecha, fecha + timedelta(seconds=5),
                                  "parado", 5_000_000_000, 0, "0.02", "0.05", 1)

    def arrancar(self):
        return crear_flota_con_recuperacion(self.ruta, lambda _: ServicioCarreras())

    def test_base_vacia_no_bloquea_usuario(self):
        arranque = self.arrancar()
        self.assertEqual(arranque.pendientes, ())
        self.assertTrue(arranque.flota.ejecutar(self.cuentas[0].id, "iniciar").activa)

    def test_tres_estados_bloquean_y_guardada_no_bloquea(self):
        puntos = [replace(self.punto, carrera_id=f"carrera-{i}", usuario_id=self.cuentas[i].id) for i in range(4)]
        guardar_punto(self.ruta, puntos[0])
        guardar_punto(self.ruta, puntos[1])
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='revision_pendiente' WHERE id=?", (puntos[1].carrera_id,))
        preparar_punto_final(self.ruta, puntos[2], puntos[2].ultimo_control)
        preparar_punto_final(self.ruta, puntos[3], puntos[3].ultimo_control)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada' WHERE id=?", (puntos[3].carrera_id,))
        arranque = self.arrancar()
        self.assertEqual({x.estado for x in arranque.pendientes}, {"activa", "revision_pendiente", "guardado_pendiente"})
        for cuenta in self.cuentas[:3]:
            self.assertTrue(arranque.flota.requiere_reconciliacion(cuenta.id))
            for operacion in (lambda: arranque.flota.ejecutar(cuenta.id, "iniciar"),
                              lambda: arranque.flota.consultar(cuenta.id),
                              lambda: arranque.flota.capturar(cuenta.id),
                              lambda: arranque.flota.guardar_captura(self.ruta, cuenta.id)):
                with self.assertRaises(FlotaBloqueada):
                    operacion()
        self.assertTrue(arranque.flota.ejecutar(self.cuentas[3].id, "iniciar").activa)
        self.assertTrue(arranque.flota.ejecutar(self.cuentas[4].id, "iniciar").activa)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT estado_registro FROM carreras_recuperacion WHERE id=?", (puntos[0].carrera_id,)).fetchone()[0], "activa")

    def test_fichero_ausente_no_se_crea_ni_devuelve_flota(self):
        inexistente = self.ruta.parent / "no-existe.sqlite3"
        with self.assertRaises(ArranqueRecuperacionError):
            crear_flota_con_recuperacion(inexistente, lambda _: ServicioCarreras())
        self.assertFalse(inexistente.exists())

    def test_base_incompatible_o_sin_tabla_falla_cerrada(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("PRAGMA user_version=99")
        with self.assertRaises(ArranqueRecuperacionError):
            self.arrancar()
        with conectar(self.ruta) as conexion:
            conexion.execute(f"PRAGMA user_version={VERSION_ESQUEMA}")
            conexion.execute("DROP TABLE carreras_recuperacion")
        with self.assertRaises(ArranqueRecuperacionError):
            self.arrancar()

    def test_reinicio_tras_commit_incierto_refleja_lo_que_quedo_en_sqlite(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        sesiones = ServicioSesiones(self.ruta)
        for cuenta, confirmar_primero in ((self.cuentas[0], False), (self.cuentas[1], True)):
            with self.subTest(confirmar_primero=confirmar_primero):
                token = sesiones.iniciar_conductor(cuenta.nombre_usuario, "ConductorPrueba123!").token
                anterior = FlotaEnMemoria(lambda _: ServicioCarreras())
                anterior.ejecutar(cuenta.id, "iniciar")
                @contextmanager
                def conexion_con_error(ruta):
                    with conectar(ruta) as real:
                        class Proxy:
                            def __getattr__(self, nombre):
                                return getattr(real, nombre)
                            def commit(self):
                                if confirmar_primero:
                                    real.commit()
                                raise OSError("Error de confirmación simulado")
                        yield Proxy()
                with patch("taxiroute.base_cuentas.conectar", conexion_con_error):
                    with self.assertRaises(OSError):
                        anterior.preparar_cierre_experimental(sesiones, token, 1)
                arranque = self.arrancar()
                self.assertEqual(arranque.flota.requiere_reconciliacion(cuenta.id), confirmar_primero)
                if confirmar_primero:
                    with self.assertRaises(FlotaBloqueada):
                        arranque.flota.ejecutar(cuenta.id, "iniciar")
                else:
                    self.assertTrue(arranque.flota.ejecutar(cuenta.id, "iniciar").activa)
    def test_varias_carreras_sin_resolver_de_misma_cuenta_impiden_arranque(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP INDEX una_carrera_pendiente_por_usuario")
        guardar_punto(self.ruta, replace(self.punto, carrera_id="carrera-extra"))
        with self.assertRaises(ArranqueRecuperacionError):
            self.arrancar()
    def test_filas_corruptas_incluida_guardada_impiden_arranque(self):
        preparar_punto_final(self.ruta, self.punto, self.punto.ultimo_control)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada'")
        for campo, valor in (("estado_registro", "inesperado"), ("ultimo_control", "fecha-mala"),
                             ("importe_final", "9.99"), ("usuario_id", "huerfano")):
            with self.subTest(campo=campo):
                with conectar(self.ruta) as conexion:
                    conexion.execute("PRAGMA foreign_keys=OFF")
                    conexion.execute("PRAGMA ignore_check_constraints=ON")
                    anterior = conexion.execute(f"SELECT {campo} FROM carreras_recuperacion").fetchone()[0]
                    conexion.execute(f"UPDATE carreras_recuperacion SET {campo}=?", (valor,))
                with self.assertRaises(ArranqueRecuperacionError):
                    self.arrancar()
                with conectar(self.ruta) as conexion:
                    conexion.execute("PRAGMA foreign_keys=OFF")
                    conexion.execute("PRAGMA ignore_check_constraints=ON")
                    conexion.execute(f"UPDATE carreras_recuperacion SET {campo}=?", (anterior,))