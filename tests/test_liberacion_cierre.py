"""Liberación de cierres conciliados con bases exclusivamente temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite, ConciliacionSQLiteError
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.liberacion_cierre import liberar_cierre_conciliado
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasLiberacionCierre(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        self.historia = Path(temporal.name) / "historial.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="FicticiaLarga123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="FicticiaLarga123!")
        with conectar(self.base) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "FicticiaLarga123!").token
        self.token_luis = self.sesiones.iniciar_conductor("Luis", "FicticiaLarga123!").token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(crear_carrera=lambda: Carrera(
            reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)), modo_durable_experimental=True)
        HistorialSQLite(self.historia, usuario_id=self.ana.id, exigir_existente=True).inicializar()
        op = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=op, accion="iniciar", revision=0)
        self.flota.aplicar_intencion_experimental(self.sesiones, self.token, op)
        self.fecha += timedelta(seconds=10)
        self.tiempo += 10_000_000_000
        self.resumen = self.flota.preparar_cierre_durable_experimental(self.sesiones, self.token, 1)

    def conciliar(self):
        return conciliar_pendiente_sqlite(self.base, self.historia, self.ana.id, self.resumen.punto.carrera_id)

    def liberar(self, token=None):
        return liberar_cierre_conciliado(self.flota, self.sesiones, token or self.token, self.historia)

    def test_cierre_y_segunda_carrera_sin_reiniciar(self):
        self.conciliar()
        resultado = self.liberar()
        self.assertEqual(str(resultado.importe), "0.20")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNone(self.flota._servicios[self.ana.id].carrera)
        op = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=op, accion="iniciar", revision=2)
        nueva = self.flota.aplicar_intencion_experimental(self.sesiones, self.token, op)
        self.assertEqual(nueva.revision, 3)
        self.assertNotEqual(nueva.carrera_id, self.resumen.punto.carrera_id)
        self.assertEqual(len(HistorialSQLite(self.historia, usuario_id=self.ana.id, exigir_existente=True).consultar()), 1)

    def test_sin_conciliar_no_libera(self):
        with self.assertRaises(OperacionNoPermitida):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNotNone(self.flota._servicios[self.ana.id].carrera)

    def test_cuenta_ajena_o_sesion_revocada_no_liberan(self):
        self.conciliar()
        with self.assertRaises(FlotaBloqueada):
            self.liberar(self.token_luis)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_historial_modificado_no_libera(self):
        import sqlite3
        from contextlib import closing
        self.conciliar()
        with closing(sqlite3.connect(self.historia)) as c:
            c.execute("UPDATE carreras SET importe='999.00'")
            c.commit()
        with self.assertRaises(ConciliacionSQLiteError):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNotNone(self.flota._servicios[self.ana.id].carrera)

    def test_repeticion_no_finaliza_otra_carrera(self):
        self.conciliar()
        self.liberar()
        with self.assertRaises(FlotaBloqueada):
            self.liberar()

    def test_dos_liberaciones_simultaneas_solo_una_tiene_exito(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.conciliar()
        barrera = Barrier(2)
        def intentar(_):
            barrera.wait(timeout=5)
            try:
                return self.liberar()
            except FlotaBloqueada:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, range(2)))
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(len(HistorialSQLite(self.historia, usuario_id=self.ana.id, exigir_existente=True).consultar()), 1)

    def test_error_al_cerrar_transaccion_restaura_bloqueo_y_carrera(self):
        from unittest.mock import patch
        from taxiroute.liberacion_cierre import _conectar_existente
        self.conciliar()
        def conexion_con_fallo(ruta):
            class Proxy:
                def __init__(self, real):
                    self.real = real
                    self.fallo = False
                def __getattr__(self, nombre):
                    return getattr(self.real, nombre)
                def rollback(self):
                    self.real.rollback()
                    if not self.fallo:
                        self.fallo = True
                        raise OSError("Fallo simulado al cerrar transacción")
            return Proxy(_conectar_existente(ruta))
        with patch("taxiroute.liberacion_cierre._conectar_existente", side_effect=conexion_con_fallo):
            with self.assertRaises(OSError):
                self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertIsNotNone(self.flota._servicios[self.ana.id].carrera)
        self.assertEqual(self.flota._cierres_preparados[self.ana.id], self.resumen)
        self.assertEqual(self.liberar().identificador, self.resumen.punto.carrera_id)
