"""Preparación durable sin historial con cuentas y relojes ficticios."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida, RevisionDesactualizada
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasCierreDurable(unittest.TestCase):
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
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", clave).token
        self.otro_token = self.sesiones.iniciar_conductor("Luis", clave).token
        self.flota = FlotaEnMemoria(
            lambda _: ServicioCarreras(crear_carrera=lambda: Carrera(
                reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)),
            modo_durable_experimental=True)
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador, accion="iniciar", revision=0)
        self.inicio = self.flota.aplicar_intencion_experimental(self.sesiones, self.token, identificador)
        self.fecha += timedelta(seconds=10)
        self.tiempo += 10_000_000_000

    def cerrar(self, token=None, revision=1):
        return self.flota.preparar_cierre_durable_experimental(self.sesiones, token or self.token, revision)

    def estado(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT estado_registro FROM carreras_recuperacion WHERE usuario_id=?",
                             (self.ana.id,)).fetchone()[0]

    def test_prepara_sin_modificar_historial_ni_finalizar_memoria(self):
        resumen = self.cerrar()
        self.assertEqual((resumen.punto.revision, str(resumen.importe)), (2, "0.20"))
        self.assertEqual(self.estado(), "guardado_pendiente")
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertFalse(self.flota._servicios[self.ana.id].carrera.finalizada)
        self.assertEqual(self.flota._cierres_preparados[self.ana.id], resumen)

    def test_revision_sesion_ajena_y_revocacion_fallan_sin_bloquear(self):
        with self.assertRaises(RevisionDesactualizada):
            self.cerrar(revision=0)
        with self.assertRaises(OperacionNoPermitida):
            self.cerrar(token=self.otro_token, revision=0)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.cerrar()
        self.assertEqual(self.estado(), "activa")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))

    def test_intencion_pendiente_impide_preparar(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=str(uuid4()),
                            accion="mover", revision=1, carrera_id=self.inicio.carrera_id)
        with self.assertRaises(OperacionNoPermitida):
            self.cerrar()
        self.assertEqual(self.estado(), "activa")

    def test_error_antes_del_commit_no_bloquea_si_no_se_preparo(self):
        with patch("taxiroute.finalizacion_pendiente.preparar_punto_final_en_transaccion",
                   side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                self.cerrar()
        self.assertEqual(self.estado(), "activa")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))

    def test_error_commit_antes_y_despues_mantiene_bloqueo(self):
        for confirmar in (False, True):
            with self.subTest(confirmar=confirmar):
                if confirmar:
                    self.setUp()
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
                        self.cerrar()
                self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
                self.assertEqual(self.estado(), "guardado_pendiente" if confirmar else "activa")
                with self.assertRaises(FlotaBloqueada):
                    self.cerrar()

    def test_dos_cierres_simultaneos_solo_uno_se_prepara(self):
        def intentar(_):
            try:
                return self.cerrar()
            except FlotaBloqueada:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, range(2)))
        self.assertEqual(sum(x is not None for x in resultados), 1)
        self.assertEqual(self.estado(), "guardado_pendiente")


if __name__ == "__main__":
    unittest.main()
