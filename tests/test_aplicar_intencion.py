"""Pruebas del protocolo experimental con cuentas y bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasAplicarIntencion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        clave = "FicticiaLarga123!"
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena=clave)
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", clave).token
        self.token_luis = self.sesiones.iniciar_conductor("Luis", clave).token
        self.id = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="iniciar", revision=0)
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras())

    def aplicar(self):
        return self.flota.aplicar_intencion_experimental(self.sesiones, self.token, self.id)

    def cantidades(self):
        with conectar(self.ruta) as c:
            return tuple(c.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]
                         for tabla in ("carreras_recuperacion", "recibos_operacion", "intenciones_operacion"))

    def test_commit_guarda_punto_recibo_elimina_intencion_y_es_idempotente(self):
        punto = self.aplicar()
        self.assertEqual(punto.usuario_id, self.ana.id)
        self.assertEqual(punto.revision, 1)
        self.assertEqual(self.cantidades(), (1, 1, 0))
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.aplicar(), punto)
        self.assertEqual(self.cantidades(), (1, 1, 0))
        arranque = crear_flota_con_recuperacion(self.ruta, lambda _: ServicioCarreras())
        self.assertEqual(len(arranque.intenciones), 0)
        self.assertTrue(arranque.flota.requiere_reconciliacion(self.ana.id))

    def test_usuario_ajeno_y_sesion_revocada_no_mutan(self):
        with self.assertRaises(OperacionNoPermitida):
            self.flota.aplicar_intencion_experimental(self.sesiones, self.token_luis, self.id)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.aplicar()
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.cantidades(), (0, 0, 1))

    def test_fallo_despues_de_mutacion_bloquea_y_revierte_transaccion(self):
        with patch("taxiroute.recibos_operacion.guardar_recibo_en_transaccion", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                self.aplicar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.cantidades(), (0, 0, 1))
        self.assertTrue(crear_flota_con_recuperacion(self.ruta, lambda _: ServicioCarreras())
                        .flota.requiere_reconciliacion(self.ana.id))

    def test_fallo_commit_antes_o_despues_y_reintento_sin_duplicacion(self):
        for confirmar in (False, True):
            with self.subTest(confirmar=confirmar):
                flota = FlotaEnMemoria(lambda _: ServicioCarreras())
                @contextmanager
                def conexion_error(ruta):
                    with conectar(ruta) as real:
                        class Proxy:
                            def __getattr__(self, nombre):
                                return getattr(real, nombre)
                            def commit(self):
                                if confirmar:
                                    real.commit()
                                raise OSError("commit incierto")
                        yield Proxy()
                with patch("taxiroute.base_cuentas.conectar", conexion_error):
                    with self.assertRaises(OSError):
                        flota.aplicar_intencion_experimental(self.sesiones, self.token, self.id)
                self.assertTrue(flota.requiere_reconciliacion(self.ana.id))
                self.assertEqual(self.cantidades(), (1, 1, 0) if confirmar else (0, 0, 1))
                if confirmar:
                    punto = flota.aplicar_intencion_experimental(self.sesiones, self.token, self.id)
                    self.assertEqual(punto.revision, 1)
                    self.assertTrue(flota.requiere_reconciliacion(self.ana.id))
                    self.assertEqual(self.cantidades(), (1, 1, 0))
                else:
                    with self.assertRaises(FlotaBloqueada):
                        flota.aplicar_intencion_experimental(self.sesiones, self.token, self.id)
                self.assertTrue(crear_flota_con_recuperacion(self.ruta, lambda _: ServicioCarreras())
                                .flota.requiere_reconciliacion(self.ana.id))

    def test_dos_llamadas_simultaneas_devuelven_mismo_punto(self):
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            puntos = list(ejecutor.map(lambda _: self.aplicar(), range(2)))
        self.assertEqual(puntos[0], puntos[1])
        self.assertEqual(self.cantidades(), (1, 1, 0))

    def test_mover_desde_punto_coincidente(self):
        with conectar(self.ruta) as c:
            c.execute("DELETE FROM intenciones_operacion")
        self.flota.ejecutar(self.ana.id, "iniciar")
        inicial = self.flota.guardar_captura(self.ruta, self.ana.id)
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="mover", revision=1, carrera_id=inicial.carrera_id)
        punto = self.aplicar()
        self.assertEqual(punto.estado_vehiculo, "en movimiento")
        self.assertEqual(punto.revision, 2)
        self.assertEqual(punto.carrera_id, inicial.carrera_id)
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))

    def test_delete_ignorado_no_confirma_ni_desbloquea(self):
        with conectar(self.ruta) as c:
            c.execute("""CREATE TRIGGER ignora_borrado BEFORE DELETE ON intenciones_operacion
                         BEGIN SELECT RAISE(IGNORE); END""")
        with self.assertRaises(OperacionNoPermitida):
            self.aplicar()
        self.assertEqual(self.cantidades(), (0, 0, 1))
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))


if __name__ == "__main__":
    unittest.main()
