"""Revisión persistente experimental, con datos exclusivamente temporales."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.conciliacion_csv import conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.revision_durable import RevisionDurableInvalida, revision_durable
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasRevisionDurable(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.base = self.carpeta / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.clave = "FicticiaLarga123!"
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        with conectar(self.base) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", self.clave).token

    def registrar(self, revision):
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador,
                            accion="iniciar", revision=revision)
        return identificador

    def test_dos_carreras_con_cierre_conciliado_y_reinicio(self):
        flota = FlotaEnMemoria(lambda _: ServicioCarreras())
        primero = flota.aplicar_intencion_experimental(self.sesiones, self.token, self.registrar(0))
        self.assertEqual(primero.revision, 1)
        flota.preparar_cierre_experimental(self.sesiones, self.token, revision=1)
        conciliar_pendiente_csv(self.base, self.carpeta / "historial.csv",
                                self.ana.id, primero.carrera_id)
        flota_nueva = crear_flota_con_recuperacion(self.base, lambda _: ServicioCarreras()).flota
        self.assertFalse(flota_nueva.requiere_reconciliacion(self.ana.id))
        with self.assertRaises(IntencionNoPermitida):
            self.registrar(0)
        segundo_id = self.registrar(2)
        segundo = flota_nueva.aplicar_intencion_experimental(self.sesiones, self.token, segundo_id)
        self.assertEqual(segundo.revision, 3)
        self.assertNotEqual(primero.carrera_id, segundo.carrera_id)
        self.assertTrue(crear_flota_con_recuperacion(self.base, lambda _: ServicioCarreras())
                        .flota.requiere_reconciliacion(self.ana.id))

    def test_recibo_sin_punto_falla_cerrado(self):
        flota = FlotaEnMemoria(lambda _: ServicioCarreras())
        flota.aplicar_intencion_experimental(self.sesiones, self.token, self.registrar(0))
        with conectar(self.base) as c:
            c.execute("DELETE FROM carreras_recuperacion")
        with conectar(self.base) as c:
            with self.assertRaises(RevisionDurableInvalida):
                revision_durable(c, self.ana.id)
        with self.assertRaises(RevisionDurableInvalida):
            self.registrar(0)


    def test_control_periodico_misma_revision_sobrevive_al_reinicio(self):
        from datetime import timedelta
        from dataclasses import replace
        from taxiroute.puntos_control import guardar_punto
        flota = FlotaEnMemoria(lambda _: ServicioCarreras())
        anterior = flota.aplicar_intencion_experimental(self.sesiones, self.token, self.registrar(0))
        punto = replace(anterior, ultimo_control=anterior.ultimo_control + timedelta(seconds=5),
                        parado_ns=anterior.parado_ns + 5_000_000_000)
        guardar_punto(self.base, punto)
        reinicio = crear_flota_con_recuperacion(self.base, lambda _: ServicioCarreras())
        self.assertTrue(reinicio.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(reinicio.flota._revisiones[self.ana.id], 1)

    def test_control_corrupto_misma_revision_se_rechaza(self):
        from datetime import timedelta
        from dataclasses import replace
        from taxiroute.puntos_control import guardar_punto
        flota = FlotaEnMemoria(lambda _: ServicioCarreras())
        anterior = flota.aplicar_intencion_experimental(self.sesiones, self.token, self.registrar(0))
        punto = replace(anterior, ultimo_control=anterior.ultimo_control + timedelta(seconds=5),
                        movimiento_ns=anterior.movimiento_ns + 1)
        guardar_punto(self.base, punto)
        with conectar(self.base) as c:
            with self.assertRaises(RevisionDurableInvalida):
                revision_durable(c, self.ana.id)


if __name__ == "__main__":
    unittest.main()
