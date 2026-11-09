"""Administración con sesiones reales de prueba y buzón simulado."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from taxiroute.administracion import administrar_conductor, AdministracionDenegada
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones


class PruebasAdministracion(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.ruta = Path(carpeta.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.fecha = datetime(2026, 11, 8, tzinfo=timezone.utc)
        clave = "FicticiaLarga123!"
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin",
                             contrasena=clave, rol="administrador")
        conductor = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                                 contrasena=clave)
        self.admin, self.conductor = admin.id, conductor.id
        self.sql("UPDATE usuarios SET cambio_obligatorio=0")
        self.sql("INSERT INTO configuracion_administrador VALUES (?, ?)",
                 (admin.id, "test@example.invalid"))
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
                          enviar=lambda correo, codigo: buzon.append(codigo),
                          reloj=lambda: self.fecha)
        desafio = pin.solicitar("Admin", clave)
        self.token = pin.verificar(desafio, buzon[-1]).sesion.token
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token_conductor = self.sesiones.iniciar_conductor("Ana", clave).token

    def sql(self, consulta, valores=()):
        with conectar(self.ruta) as c:
            c.execute(consulta, valores)

    def fila(self):
        with conectar(self.ruta) as c:
            return dict(c.execute("SELECT * FROM usuarios WHERE id=?", (self.conductor,)).fetchone())

    def operar(self, accion, token=None, destino=None):
        return administrar_conductor(self.ruta, self.token if token is None else token,
            self.conductor if destino is None else destino, accion, reloj=lambda: self.fecha)

    def test_desactivar_revoca_y_reactivar_no_recupera_sesion(self):
        self.operar("desactivar")
        self.assertEqual(self.fila()["habilitado"], 0)
        self.operar("reactivar")
        self.assertEqual(self.fila()["habilitado"], 1)
        self.assertIsNone(self.sesiones.consultar(self.token_conductor))
        with conectar(self.ruta) as c:
            self.assertEqual([f["accion"] for f in c.execute("SELECT * FROM auditoria_cuentas")],
                             ["desactivar", "reactivar"])

    def test_desbloqueo_y_desactivacion_son_independientes(self):
        self.sql("UPDATE usuarios SET habilitado=0, intentos_fallidos=15, bloqueado_en=? WHERE id=?",
                 (self.fecha.isoformat(), self.conductor))
        self.operar("desbloquear")
        fila = self.fila()
        self.assertEqual((fila["habilitado"], fila["intentos_fallidos"]), (0, 0))
        self.assertIsNone(fila["bloqueado_en"])
        self.sql("UPDATE usuarios SET intentos_fallidos=15, bloqueado_en=? WHERE id=?",
                 (self.fecha.isoformat(), self.conductor))
        self.operar("reactivar")
        self.assertIsNotNone(self.fila()["bloqueado_en"])

    def test_revocar_no_desactiva(self):
        self.operar("revocar")
        self.assertEqual(self.fila()["habilitado"], 1)
        self.assertIsNone(self.sesiones.consultar(self.token_conductor))

    def test_denegados_no_alteran_datos(self):
        antes = self.fila()
        for token, destino, accion in (
            (self.token_conductor, self.conductor, "desactivar"),
            ("x"*43, self.conductor, "desactivar"),
            (self.token, self.admin, "desactivar"),
            (self.token, self.conductor, "borrar"),
        ):
            with self.assertRaises(AdministracionDenegada):
                self.operar(accion, token, destino)
        self.assertEqual(self.fila(), antes)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM auditoria_cuentas").fetchone()[0], 0)

    def test_sesion_admin_caducada(self):
        self.fecha += timedelta(hours=1)
        with self.assertRaises(AdministracionDenegada):
            self.operar("desactivar")
        self.assertEqual(self.fila()["habilitado"], 1)

    def test_validacion_compartida_rechaza_cambio_de_correo(self):
        self.sql("UPDATE configuracion_administrador SET correo_verificacion=? WHERE usuario_id=?",
                 ("otro@example.invalid", self.admin))
        with self.assertRaises(AdministracionDenegada):
            self.operar("desactivar")
        self.assertIsNone(self.sesiones.consultar(self.token))
        self.assertEqual(self.fila()["habilitado"], 1)

    def test_fallo_auditoria_revierte_desactivacion_y_revocacion(self):
        self.sql("""CREATE TRIGGER fallo_auditoria BEFORE INSERT ON auditoria_cuentas
                    BEGIN SELECT RAISE(ABORT, 'simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.operar("desactivar")
        self.assertEqual(self.fila()["habilitado"], 1)
        self.assertIsNotNone(self.sesiones.consultar(self.token_conductor))
