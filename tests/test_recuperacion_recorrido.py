"""Recorridos de recuperación con datos y archivos exclusivamente temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.conciliacion_csv import conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasRecorridoRecuperacion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        self.csv = Path(temporal.name) / "historial.csv"
        inicializar_base(self.base)
        self.usuario = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana",
                                    contrasena="ConductorPrueba123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.usuario.id,))
        self.fecha = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota = FlotaEnMemoria(self.crear_servicio)

    def crear_servicio(self, usuario):
        return ServicioCarreras(crear_carrera=lambda: Carrera(
            reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha))

    def avanzar(self, segundos):
        self.tiempo += segundos * 1_000_000_000
        self.fecha += timedelta(seconds=segundos)

    def reiniciar(self):
        return crear_flota_con_recuperacion(self.base, self.crear_servicio)

    def test_caida_durante_carrera_bloquea_al_reiniciar_y_conserva_captura(self):
        self.flota.ejecutar(self.usuario.id, "iniciar")
        self.avanzar(5)
        punto = self.flota.guardar_captura(self.base, self.usuario.id)
        self.flota.ejecutar(self.usuario.id, "mover")
        self.avanzar(2)
        reiniciada = self.reiniciar()
        self.assertEqual([(x.usuario_id, x.carrera_id, x.estado) for x in reiniciada.pendientes],
                         [(self.usuario.id, punto.carrera_id, "activa")])
        with self.assertRaises(FlotaBloqueada):
            reiniciada.flota.ejecutar(self.usuario.id, "iniciar")
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=?", (punto.carrera_id,)).fetchone()
        self.assertEqual((fila["parado_ns"], fila["estado_vehiculo"]), (5_000_000_000, "parado"))
        self.assertFalse(self.csv.exists())

    def test_reinicio_tras_cierre_pendiente_permite_conciliar_una_sola_vez(self):
        self.flota.ejecutar(self.usuario.id, "iniciar")
        self.avanzar(5)
        self.flota.guardar_captura(self.base, self.usuario.id)
        self.flota.ejecutar(self.usuario.id, "mover")
        self.avanzar(10)
        pendiente = self.flota.preparar_cierre_experimental(self.sesiones, self.token, 2)
        reiniciada = self.reiniciar()
        self.assertEqual(reiniciada.pendientes[0].estado, "guardado_pendiente")
        with self.assertRaises(FlotaBloqueada):
            reiniciada.flota.ejecutar(self.usuario.id, "iniciar")
        resultado = conciliar_pendiente_csv(self.base, self.csv, self.usuario.id, pendiente.punto.carrera_id)
        self.assertFalse(resultado.ya_guardada)
        self.assertTrue(conciliar_pendiente_csv(self.base, self.csv, self.usuario.id, pendiente.punto.carrera_id).ya_guardada)
        filas = HistorialCSV(self.csv).consultar()
        self.assertEqual(len(filas), 1)
        self.assertEqual((filas[0]["identificador"], filas[0]["importe"]),
                         (pendiente.punto.carrera_id, "0.60"))
        final = self.reiniciar()
        self.assertEqual(final.pendientes, ())
        self.assertTrue(final.flota.ejecutar(self.usuario.id, "iniciar").activa)

    def test_cierre_pendiente_y_reinicio_con_historial_sqlite(self):
        from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite
        from taxiroute.historial_sqlite import HistorialSQLite
        destino = self.base.parent / "historial.sqlite3"
        historial = HistorialSQLite(destino, usuario_id=self.usuario.id, exigir_existente=True)
        historial.inicializar()
        self.flota.ejecutar(self.usuario.id, "iniciar")
        self.avanzar(5)
        self.flota.guardar_captura(self.base, self.usuario.id)
        self.flota.ejecutar(self.usuario.id, "mover")
        self.avanzar(10)
        pendiente = self.flota.preparar_cierre_experimental(self.sesiones, self.token, 2)
        self.assertEqual(self.reiniciar().pendientes[0].estado, "guardado_pendiente")
        self.assertFalse(conciliar_pendiente_sqlite(self.base, destino, self.usuario.id, pendiente.punto.carrera_id))
        self.assertTrue(conciliar_pendiente_sqlite(self.base, destino, self.usuario.id, pendiente.punto.carrera_id))
        self.assertEqual([(x["identificador"], x["importe"]) for x in historial.consultar()],
                         [(pendiente.punto.carrera_id, "0.60")])
        self.assertEqual(self.reiniciar().pendientes, ())
