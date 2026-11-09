"""Recuperación interna con bases y códigos ficticios, sin correo ni servidor."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.inicializacion_administrador import preparar_primer_administrador_con_codigos
from taxiroute.pin import ServicioPIN
from taxiroute.recuperacion_admin_experimental import RecuperacionDenegada, recuperar_administrador
from taxiroute.sesiones import ServicioSesiones
from taxiroute.cuentas import _PROTECTOR


class PruebasRecuperacionAdmin(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdminPrueba123!"
        self.preparada = preparar_primer_administrador_con_codigos(self.ruta, nombre_usuario="AdminPrueba",
            nombre_visible="Prueba", contrasena=self.clave, correo="prueba@example.invalid")
        self.usuario = self.preparada.cuenta.id
        self.ahora = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)
        self.buzon = []
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), reloj=lambda: self.ahora,
            enviar=lambda destinatario, codigo: self.buzon.append(codigo))
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.ahora)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (self.usuario,))
        primer_desafio = self.pin.solicitar("AdminPrueba", self.clave)
        self.token = self.pin.verificar(primer_desafio, self.buzon[-1]).sesion.token
        self.ahora += timedelta(minutes=2)
        self.desafio = self.pin.solicitar("AdminPrueba", self.clave)
        self.codigo_pin = self.buzon[-1]
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET intentos_fallidos=15, bloqueado_en=?, proximo_intento_en=NULL WHERE id=?",
                             (self.ahora.isoformat(), self.usuario))

    def recuperar(self, *, clave=None, codigo=None):
        return recuperar_administrador(self.ruta, "AdminPrueba", self.clave if clave is None else clave,
            self.preparada.codigos[0] if codigo is None else codigo, reloj=lambda: self.ahora)

    def estado(self):
        with conectar(self.ruta) as conexion:
            usuario = dict(conexion.execute("SELECT * FROM usuarios WHERE id=?", (self.usuario,)).fetchone())
            codigos = [dict(fila) for fila in conexion.execute("SELECT * FROM codigos_recuperacion WHERE usuario_id=?", (self.usuario,))]
            auditoria = [dict(fila) for fila in conexion.execute("SELECT * FROM auditoria_cuentas")]
            sesiones = [dict(fila) for fila in conexion.execute("SELECT * FROM sesiones")]
            desafio = dict(conexion.execute("SELECT * FROM desafios_acceso WHERE id=?", (self.desafio,)).fetchone())
        return usuario, codigos, auditoria, sesiones, desafio

    def test_desbloquea_con_ambos_factores_y_revoca_sin_iniciar_sesion(self):
        self.recuperar()
        usuario, codigos, auditoria, sesiones, desafio = self.estado()
        self.assertIsNone(usuario["bloqueado_en"])
        self.assertEqual(usuario["intentos_fallidos"], 0)
        self.assertEqual(usuario["version_credenciales"], 2)
        self.assertEqual(sum(fila["usado_en"] is not None for fila in codigos), 1)
        self.assertEqual(len(sesiones), 1)
        self.assertIsNotNone(sesiones[0]["revocada_en"])
        self.assertIsNone(self.sesiones.consultar(self.token))
        self.assertIsNotNone(desafio["invalidado_en"])
        self.assertIsNone(self.pin.verificar(self.desafio, self.codigo_pin))
        self.assertEqual([(x["actor_id"], x["afectado_id"], x["accion"]) for x in auditoria],
                         [(self.usuario, self.usuario, "desbloquear")])
        with self.assertRaises(RecuperacionDenegada):
            self.recuperar()

    def test_clave_o_codigo_incorrecto_aplica_espera_y_no_consume(self):
        with self.assertRaises(RecuperacionDenegada):
            self.recuperar(clave="incorrecta")
        usuario, codigos, auditoria, sesiones, _ = self.estado()
        self.assertEqual(usuario["intentos_fallidos"], 16)
        self.assertTrue(all(fila["usado_en"] is None for fila in codigos))
        self.assertEqual(auditoria, [])
        self.assertIsNone(sesiones[0]["revocada_en"])
        with self.assertRaises(RecuperacionDenegada):
            self.recuperar()
        self.ahora += timedelta(seconds=60)
        with self.assertRaises(RecuperacionDenegada):
            self.recuperar(codigo="incorrecto")
        self.ahora += timedelta(seconds=60)
        self.recuperar()

    def test_cuenta_desactivada_no_se_reactiva(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=0 WHERE id=?", (self.usuario,))
        with self.assertRaises(RecuperacionDenegada):
            self.recuperar()
        usuario, codigos, auditoria, _, _ = self.estado()
        self.assertEqual(usuario["habilitado"], 0)
        self.assertTrue(all(fila["usado_en"] is None for fila in codigos))
        self.assertEqual(auditoria, [])

    def test_fallo_de_auditoria_revierte_codigo_y_todos_los_cambios(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_auditoria BEFORE INSERT ON auditoria_cuentas
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.recuperar()
        usuario, codigos, auditoria, sesiones, desafio = self.estado()
        self.assertIsNotNone(usuario["bloqueado_en"])
        self.assertTrue(all(fila["usado_en"] is None for fila in codigos))
        self.assertIsNone(sesiones[0]["revocada_en"])
        self.assertIsNone(desafio["invalidado_en"])
        self.assertEqual(auditoria, [])

    def test_dos_intentos_simultaneos_solo_recuperan_una_vez(self):
        def intentar(_):
            try:
                self.recuperar()
                return True
            except RecuperacionDenegada:
                return False
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            self.assertEqual(sum(ejecutor.map(intentar, (1, 2))), 1)
        _, codigos, auditoria, _, _ = self.estado()
        self.assertEqual(sum(fila["usado_en"] is not None for fila in codigos), 1)
        self.assertEqual(len(auditoria), 1)

    def test_cambio_de_credenciales_durante_verificacion_invalida_intento(self):
        verificar = _PROTECTOR.verify
        def cambiar_durante_verificacion(*argumentos):
            resultado = verificar(*argumentos)
            with conectar(self.ruta) as conexion:
                conexion.execute("UPDATE usuarios SET version_credenciales=version_credenciales+1 WHERE id=?", (self.usuario,))
            return resultado
        with patch("taxiroute.recuperacion_admin_experimental._PROTECTOR", Mock(verify=cambiar_durante_verificacion)):
            with self.assertRaises(RecuperacionDenegada):
                self.recuperar()
        usuario, codigos, auditoria, _, _ = self.estado()
        self.assertIsNotNone(usuario["bloqueado_en"])
        self.assertTrue(all(fila["usado_en"] is None for fila in codigos))
        self.assertEqual(auditoria, [])


if __name__ == "__main__":
    unittest.main()
