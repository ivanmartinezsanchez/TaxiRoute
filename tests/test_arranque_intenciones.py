"""Arranque desde un diario SQLite temporal; no ejecuta las intenciones."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, crear_flota_con_recuperacion
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasArranqueIntenciones(unittest.TestCase):
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
        self.id = str(uuid4())

    def arrancar(self):
        return crear_flota_con_recuperacion(self.ruta, lambda _: ServicioCarreras())

    def test_reinicio_bloquea_propietaria_y_conserva_diario(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="iniciar", revision=0)
        for _ in range(2):
            arranque = self.arrancar()
            self.assertEqual(arranque.pendientes, ())
            self.assertEqual(arranque.intenciones[0].id, self.id)
            self.assertTrue(arranque.flota.requiere_reconciliacion(self.ana.id))
            with self.assertRaises(FlotaBloqueada):
                arranque.flota.ejecutar(self.ana.id, "iniciar")
            self.assertTrue(arranque.flota.ejecutar(self.luis.id, "iniciar").activa)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)

    def test_datos_corruptos_bloquean_arranque(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="iniciar", revision=0)
        for campo, valor in (("id", "no-uuid"), ("usuario_id", "inexistente"),
                             ("accion", "incorrecta"), ("revision", -1),
                             ("revision", 1), ("creada_en", "sin-fecha"),
                             ("creada_en", "2026-11-09T12:00:00"),
                             ("carrera_id", "no-corresponde")):
            with self.subTest(campo=campo, valor=valor):
                with conectar(self.ruta) as c:
                    c.execute("PRAGMA foreign_keys=OFF")
                    c.execute("PRAGMA ignore_check_constraints=ON")
                    anterior = c.execute(f"SELECT {campo} FROM intenciones_operacion").fetchone()[0]
                    c.execute(f"UPDATE intenciones_operacion SET {campo}=?", (valor,))
                with self.assertRaises(ArranqueRecuperacionError):
                    self.arrancar()
                with conectar(self.ruta) as c:
                    c.execute("PRAGMA foreign_keys=OFF")
                    c.execute("PRAGMA ignore_check_constraints=ON")
                    c.execute(f"UPDATE intenciones_operacion SET {campo}=?", (anterior,))

    def test_intencion_y_carrera_de_la_misma_cuenta_se_combinan(self):
        punto = PuntoControl("carrera-ana", self.ana.id, self.fecha, self.fecha,
                             "parado", 0, 0, "0.02", "0.05", 1)
        guardar_punto(self.ruta, punto)
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="mover", revision=1, carrera_id=punto.carrera_id)
        arranque = self.arrancar()
        self.assertEqual(len(arranque.pendientes), 1)
        self.assertEqual(len(arranque.intenciones), 1)
        self.assertTrue(arranque.flota.requiere_reconciliacion(self.ana.id))
        self.assertFalse(arranque.flota.requiere_reconciliacion(self.luis.id))

    def test_tabla_ausente_y_intenciones_duplicadas_fallan_cerrado(self):
        registrar_intencion(self.sesiones, self.token, id_intencion=self.id,
                            accion="iniciar", revision=0)
        with conectar(self.ruta) as c:
            c.execute("DROP INDEX una_intencion_por_usuario")
            c.execute("""INSERT INTO intenciones_operacion
                SELECT ?, usuario_id, accion, revision, carrera_id, creada_en
                FROM intenciones_operacion""", (str(uuid4()),))
        with self.assertRaises(ArranqueRecuperacionError):
            self.arrancar()
        with conectar(self.ruta) as c:
            c.execute("DROP TABLE intenciones_operacion")
        with self.assertRaises(ArranqueRecuperacionError):
            self.arrancar()


if __name__ == "__main__":
    unittest.main()
