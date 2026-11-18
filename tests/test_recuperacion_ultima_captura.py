"""Recuperación experimental de último punto; solo bases temporales."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.puntos_control import PuntoControl, guardar_punto, leer_punto
from taxiroute.recuperacion_ultima_captura import revisar_ultima_captura, cerrar_en_ultima_captura, liberar_recuperada
from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasUltimaCaptura(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.base = Path(carpeta.name) / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="ConductorPrueba123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="ConductorPrueba123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.inicio = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.fecha = self.inicio + timedelta(hours=4)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.token_luis = self.sesiones.iniciar_conductor("Luis", "ConductorPrueba123!").token
        self.punto = PuntoControl("carrera-1", self.ana.id, self.inicio, self.inicio + timedelta(seconds=30),
                                  "en movimiento", 10_000_000_000, 20_000_000_000, "0.02", "0.05", 1)
        guardar_punto(self.base, self.punto)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(), usuarios_pendientes=[self.ana.id],
                                    modo_durable_experimental=True, revisiones_iniciales={self.ana.id: 1})

    def revisar(self, token=None):
        return revisar_ultima_captura(self.sesiones, token or self.token, self.flota)

    def cerrar(self, token=None, revision=1, instante=None):
        return cerrar_en_ultima_captura(self.sesiones, token or self.token, self.flota,
                                        revision_esperada=revision,
                                        ultimo_control_esperado=instante or self.punto.ultimo_control)

    def test_revisar_y_cerrar_sin_cobrar_cuatro_horas(self):
        self.assertEqual(self.revisar(), (self.punto, "activa"))
        resumen = self.cerrar()
        self.assertEqual(resumen.fin, self.punto.ultimo_control)
        self.assertEqual(str(resumen.importe), "1.20")
        self.assertEqual((resumen.punto.parado_ns, resumen.punto.movimiento_ns), (10_000_000_000, 20_000_000_000))
        self.assertEqual(resumen.punto.revision, 2)
        self.assertEqual(self.cerrar(), resumen)
        self.assertEqual(self.revisar(), (resumen.punto, "guardado_pendiente"))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def marcar_revision(self):
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='revision_pendiente' WHERE id=?", (self.punto.carrera_id,))

    def test_punto_en_revision_se_cierra_en_ultimo_instante_y_reintenta(self):
        self.marcar_revision()
        self.assertEqual(self.revisar(), (self.punto, "revision_pendiente"))
        resumen = self.cerrar()
        self.assertEqual(resumen.fin, self.punto.ultimo_control)
        self.assertEqual(str(resumen.importe), "1.20")
        self.assertEqual((resumen.punto.parado_ns, resumen.punto.movimiento_ns),
                         (self.punto.parado_ns, self.punto.movimiento_ns))
        self.assertEqual(resumen.punto.revision, self.punto.revision + 1)
        self.assertEqual(self.cerrar(), resumen)
        self.assertEqual(self.revisar(), (resumen.punto, "guardado_pendiente"))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_revision_revocada_o_ajena_no_modifica_carrera(self):
        self.marcar_revision()
        with self.assertRaises(OperacionNoPermitida):
            self.cerrar(self.token_luis)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.cerrar()
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT estado_registro, revision, fin FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()
            self.assertEqual(tuple(fila), ("revision_pendiente", self.punto.revision, None))

    def test_revision_con_intencion_no_permite_cierre(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=str(uuid4()),
                            accion="parar", revision=1, carrera_id=self.punto.carrera_id)
        self.marcar_revision()
        with self.assertRaises(FlotaBloqueada):
            self.cerrar()
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT estado_registro, revision FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()
            self.assertEqual(tuple(fila), ("revision_pendiente", 1))
            self.assertEqual(conexion.execute("SELECT count(*) FROM intenciones_operacion").fetchone()[0], 1)

    def test_revision_error_antes_de_confirmar_revierte_y_admite_reintento(self):
        self.marcar_revision()
        with patch("taxiroute.recuperacion_ultima_captura._finalizar_en_transaccion", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                self.cerrar()
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT estado_registro, revision, fin FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()
            self.assertEqual(tuple(fila), ("revision_pendiente", 1, None))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(str(self.cerrar().importe), "1.20")

    def test_revision_desactualizada_o_huella_distinta_no_cierra(self):
        self.marcar_revision()
        with self.assertRaises(OperacionNoPermitida):
            self.cerrar(revision=0)
        with self.assertRaises(OperacionNoPermitida):
            cerrar_en_ultima_captura(self.sesiones, self.token, self.flota, revision_esperada=1,
                                    ultimo_control_esperado=self.punto.ultimo_control, huella_esperada="0" * 64)
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT estado_registro, revision, fin FROM carreras_recuperacion WHERE id=?", (self.punto.carrera_id,)).fetchone()
            self.assertEqual(tuple(fila), ("revision_pendiente", 1, None))

    def test_usuario_ajeno_y_sesion_revocada_sin_cambios(self):
        with self.assertRaises(OperacionNoPermitida):
            self.revisar(self.token_luis)
        with self.assertRaises(OperacionNoPermitida):
            self.cerrar(self.token_luis)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.cerrar()
        self.assertEqual(leer_punto(self.base, self.ana.id, "carrera-1"), self.punto)

    def test_punto_cambia_tras_pantalla_obliga_a_revisar(self):
        self.revisar()
        actualizado = PuntoControl(self.punto.carrera_id, self.punto.usuario_id, self.punto.inicio,
            self.punto.ultimo_control + timedelta(seconds=1), self.punto.estado_vehiculo,
            self.punto.parado_ns, self.punto.movimiento_ns + 1_000_000_000,
            self.punto.tarifa_parado, self.punto.tarifa_movimiento, 2)
        guardar_punto(self.base, actualizado)
        with self.assertRaisesRegex(OperacionNoPermitida, "ha cambiado"):
            self.cerrar()
        self.assertEqual(leer_punto(self.base, self.ana.id, "carrera-1"), actualizado)
        resumen = self.cerrar(revision=2, instante=actualizado.ultimo_control)
        self.assertEqual(resumen.punto.revision, 3)

    def test_intencion_pendiente_impide_recuperacion(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=str(uuid4()),
                            accion="parar", revision=1, carrera_id=self.punto.carrera_id)
        with self.assertRaises(FlotaBloqueada):
            self.cerrar()
        self.assertEqual(leer_punto(self.base, self.ana.id, "carrera-1"), self.punto)

    def test_error_antes_de_commit_revierte_y_puede_reintentar(self):
        with patch("taxiroute.recuperacion_ultima_captura._finalizar_en_transaccion", side_effect=OSError("simulado")):
            with self.assertRaises(OSError):
                self.cerrar()
        self.assertEqual(leer_punto(self.base, self.ana.id, "carrera-1"), self.punto)
        self.assertEqual(str(self.cerrar().importe), "1.20")

    def test_conciliar_liberar_reintentar_e_iniciar_otra_carrera(self):
        historial = self.base.parent / "historial.sqlite3"
        HistorialSQLite(historial, usuario_id=self.ana.id, exigir_existente=True).inicializar()
        pendiente = self.cerrar()
        conciliar_pendiente_sqlite(self.base, historial, self.ana.id, pendiente.punto.carrera_id)
        def liberar():
            return liberar_recuperada(self.sesiones, self.token, self.flota, historial,
                revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control)
        self.assertEqual(str(liberar().importe), "1.20")
        self.assertEqual(str(liberar().importe), "1.20")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        nuevo_id = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=nuevo_id, accion="iniciar", revision=2)
        nuevo = self.flota.aplicar_intencion_experimental(self.sesiones, self.token, nuevo_id)
        self.assertEqual(nuevo.revision, 3)
        self.assertNotEqual(nuevo.carrera_id, self.punto.carrera_id)
        with self.assertRaises(FlotaBloqueada):
            liberar()

    def test_liberacion_sin_historial_o_ajena_conserva_bloqueo(self):
        historial = self.base.parent / "historial.sqlite3"
        HistorialSQLite(historial, usuario_id=self.ana.id, exigir_existente=True).inicializar()
        self.cerrar()
        with self.assertRaises(FlotaBloqueada):
            liberar_recuperada(self.sesiones, self.token, self.flota, historial,
                revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        with self.assertRaises((OperacionNoPermitida, FlotaBloqueada)):
            liberar_recuperada(self.sesiones, self.token_luis, self.flota, historial,
                revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control)

    def test_historial_alterado_no_libera_ni_cambia_importe_original(self):
        historial = self.base.parent / "historial.sqlite3"
        HistorialSQLite(historial, usuario_id=self.ana.id, exigir_existente=True).inicializar()
        pendiente = self.cerrar()
        conciliar_pendiente_sqlite(self.base, historial, self.ana.id, pendiente.punto.carrera_id)
        import sqlite3
        with closing(sqlite3.connect(historial)) as conexion:
            conexion.execute("UPDATE carreras SET importe='99.00'")
            conexion.commit()
        with self.assertRaises(ValueError):
            liberar_recuperada(self.sesiones, self.token, self.flota, historial,
                revision_esperada=1, ultimo_control_esperado=self.punto.ultimo_control)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        with conectar(self.base) as conexion:
            fila = conexion.execute("SELECT importe_final FROM carreras_recuperacion WHERE id=?",
                                    (self.punto.carrera_id,)).fetchone()
            self.assertEqual(fila[0], "1.20")
