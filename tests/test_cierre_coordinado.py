"""Preparación de cierre con sesión y captura coordinada, en bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, RevisionDesactualizada
from taxiroute.puntos_control import leer_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasCierreCoordinado(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(
            crear_carrera=lambda: Carrera(reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)))
        self.flota.ejecutar(self.ana.id, "iniciar")
        self.flota.ejecutar(self.luis.id, "iniciar")
        self.fecha += timedelta(seconds=10)
        self.tiempo = 10_000_000_000

    def estado_guardado(self):
        with conectar(self.ruta) as conexion:
            fila = conexion.execute("SELECT estado_registro FROM carreras_recuperacion WHERE usuario_id=?", (self.ana.id,)).fetchone()
            return fila[0] if fila else None

    def test_exito_bloquea_ana_y_no_afecta_a_luis(self):
        resumen = self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertEqual((resumen.punto.usuario_id, resumen.punto.revision, str(resumen.importe)),
                         (self.ana.id, 2, "0.20"))
        self.assertEqual(self.estado_guardado(), "guardado_pendiente")
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        for operacion in (lambda: self.flota.ejecutar(self.ana.id, "finalizar"),
                          lambda: self.flota.ejecutar_si_revision(self.ana.id, "mover", 1),
                          lambda: self.flota.guardar_captura(self.ruta, self.ana.id),
                          lambda: self.flota.consultar(self.ana.id),
                          lambda: self.flota.capturar(self.ana.id)):
            with self.assertRaises(FlotaBloqueada):
                operacion()
        self.assertEqual(self.flota.ejecutar(self.luis.id, "mover").estado, "en movimiento")

    def test_sesion_revocada_y_revision_antigua_no_alteran_datos(self):
        with self.assertRaises(RevisionDesactualizada):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 0)
        with self.assertRaises(ValueError):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, True)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertIsNone(self.estado_guardado())
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertTrue(self.flota.consultar(self.ana.id).activa)

    def test_fallo_previo_a_commit_no_congela_carrera(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_final BEFORE UPDATE ON carreras_recuperacion
                WHEN NEW.estado_registro='guardado_pendiente'
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertIsNone(self.estado_guardado())
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertTrue(self.flota.consultar(self.ana.id).activa)

    def test_error_commit_antes_y_despues_mantiene_bloqueo(self):
        for confirmar_primero in (False, True):
            with self.subTest(confirmar_primero=confirmar_primero):
                if confirmar_primero:
                    self.setUp()
                @contextmanager
                def conexion_con_error(ruta):
                    with conectar(ruta) as real:
                        class Proxy:
                            def __getattr__(self, nombre):
                                return getattr(real, nombre)
                            def commit(self):
                                if confirmar_primero:
                                    real.commit()
                                raise OSError("Error simulado en commit")
                        yield Proxy()
                with patch("taxiroute.base_cuentas.conectar", conexion_con_error):
                    with self.assertRaises(OSError):
                        self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
                self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
                self.assertEqual(self.estado_guardado(), "guardado_pendiente" if confirmar_primero else None)
                with self.assertRaises(FlotaBloqueada):
                    self.flota.ejecutar(self.ana.id, "mover")

    def test_token_inventado_cuenta_deshabilitada_y_cierre_repetido(self):
        with self.assertRaises(SesionNoAutorizada):
            self.flota.preparar_cierre_experimental(self.sesiones, "x" * 43, 1)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=0 WHERE id=?", (self.ana.id,))
        with self.assertRaises(SesionNoAutorizada):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertIsNone(self.estado_guardado())
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=1 WHERE id=?", (self.ana.id,))
        self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        with self.assertRaises(FlotaBloqueada):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)

    def test_fallo_previo_conserva_ultimo_punto(self):
        anterior = self.flota.guardar_captura(self.ruta, self.ana.id)
        self.tiempo += 5_000_000_000
        self.fecha += timedelta(seconds=5)
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER falla_final BEFORE UPDATE ON carreras_recuperacion
                WHEN NEW.estado_registro='guardado_pendiente'
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertEqual(leer_punto(self.ruta, self.ana.id, anterior.carrera_id), anterior)
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))

    def test_reloj_monotonico_retrocedido_no_prepara_ni_bloquea(self):
        self.tiempo = -1
        with self.assertRaises(ValueError):
            self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNone(self.estado_guardado())
    def test_accion_primero_desactualiza_la_revision_del_cierre(self):
        dentro, liberar, intento = Event(), Event(), Event()
        def mover(identidad):
            dentro.set()
            if not liberar.wait(3):
                raise TimeoutError("La prueba no liberó la acción")
            return self.flota.ejecutar(identidad.usuario_id, "mover")
        def accion():
            return self.sesiones.ejecutar_con_sesion_vigente(
                self.token, roles={"conductor"}, operacion=mover)
        def cierre():
            intento.set()
            return self.flota.preparar_cierre_experimental(self.sesiones, self.token, 1)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            futura_accion = ejecutor.submit(accion)
            self.assertTrue(dentro.wait(3))
            futuro_cierre = ejecutor.submit(cierre)
            try:
                self.assertTrue(intento.wait(3))
            finally:
                liberar.set()
            self.assertEqual(futura_accion.result(timeout=4).revision, 2)
            with self.assertRaises(RevisionDesactualizada):
                futuro_cierre.result(timeout=4)
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNone(self.estado_guardado())
        self.assertEqual(self.flota.consultar(self.ana.id).estado, "en movimiento")
    def test_accion_concurrente_espera_y_luego_es_bloqueada(self):
        dentro_commit, liberar, intento, terminada = Event(), Event(), Event(), Event()
        @contextmanager
        def conexion_con_pausa(ruta):
            with conectar(ruta) as real:
                class Proxy:
                    def __getattr__(self, nombre):
                        return getattr(real, nombre)
                    def commit(self):
                        dentro_commit.set()
                        if not liberar.wait(3):
                            raise TimeoutError("La prueba no liberó el commit")
                        real.commit()
                yield Proxy()
        def mover():
            intento.set()
            try:
                return self.flota.ejecutar(self.ana.id, "mover")
            finally:
                terminada.set()
        with patch("taxiroute.base_cuentas.conectar", conexion_con_pausa):
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                futuro = ejecutor.submit(self.flota.preparar_cierre_experimental, self.sesiones, self.token, 1)
                self.assertTrue(dentro_commit.wait(3))
                otra_accion = ejecutor.submit(mover)
                try:
                    self.assertTrue(intento.wait(3))
                    self.assertFalse(terminada.wait(0.15))
                finally:
                    liberar.set()
                self.assertEqual(str(futuro.result(timeout=4).importe), "0.20")
                with self.assertRaises(FlotaBloqueada):
                    otra_accion.result(timeout=4)