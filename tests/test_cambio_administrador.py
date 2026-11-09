"""Autorización tras PIN y cambio administrativo, siempre con datos temporales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from taxiroute.acceso import CambioContrasenaDenegado
from taxiroute.base_cuentas import (
    conectar, inicializar_base, CREAR_USUARIOS, MIGRACION_PIN, IDENTIFICADOR_APLICACION, VERSION_ESQUEMA,
)
from taxiroute.cambio_administrador import cambiar_contrasena_administrador
from taxiroute.cuentas import crear_cuenta, DatosCuentaInvalidos
from taxiroute.pin import ServicioPIN


class PruebasCambioAdministrador(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.actual = "ActualFicticia123!"
        self.nueva = "NuevaFicticia456!"
        self.fecha = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)
        cuenta = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Prueba",
                              contrasena=self.actual, rol="administrador")
        self.usuario_id = cuenta.id
        self.ejecutar("INSERT INTO configuracion_administrador VALUES (?, ?)",
                     (cuenta.id, "pruebas@example.invalid"))
        self.buzon = []
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
            enviar=lambda correo, codigo: self.buzon.append(codigo), reloj=lambda: self.fecha)

    def ejecutar(self, sql, valores=()):
        with conectar(self.ruta) as c:
            c.execute(sql, valores)

    def autorizar(self):
        desafio = self.pin.solicitar("Admin", self.actual)
        return self.pin.verificar(desafio, self.buzon[-1])

    def usuario(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM usuarios WHERE id=?", (self.usuario_id,)).fetchone()

    def cambiar(self, token, nueva=None):
        return cambiar_contrasena_administrador(self.ruta, token,
            self.nueva if nueva is None else nueva, reloj=lambda: self.fecha)

    def test_cambio_consumo_version_y_sin_sesion(self):
        self.ejecutar("UPDATE usuarios SET intentos_fallidos=2")
        resultado = self.autorizar()
        token = resultado.autorizacion_cambio
        self.assertNotIn(token, repr(resultado))
        with conectar(self.ruta) as c:
            fila = c.execute("SELECT * FROM autorizaciones_cambio").fetchone()
            self.assertEqual(fila["hash_token"], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(token, str(dict(fila)))
        self.cambiar(token)
        fila = self.usuario()
        self.assertTrue(PasswordHasher().verify(fila["hash_contrasena"], self.nueva))
        with self.assertRaises(VerifyMismatchError):
            PasswordHasher().verify(fila["hash_contrasena"], self.actual)
        self.assertEqual(fila["version_credenciales"], 2)
        self.assertEqual(fila["cambio_obligatorio"], 0)
        self.assertEqual(fila["intentos_fallidos"], 2)
        self.assertIsNone(fila["ultimo_acceso_en"])
        with self.assertRaises(CambioContrasenaDenegado):
            self.cambiar(token)
        with conectar(self.ruta) as c:
            self.assertIsNotNone(c.execute("SELECT consumido_en FROM autorizaciones_cambio").fetchone()[0])

    def test_sin_pin_no_hay_autorizacion_y_no_se_aceptan_objetos(self):
        desafio = self.pin.solicitar("Admin", self.actual)
        antes = dict(self.usuario())
        for falso in (True, {"pin_correcto": True}, desafio, "x" * 43, None):
            with self.subTest(tipo=type(falso)), self.assertRaises(CambioContrasenaDenegado):
                self.cambiar(falso)
        self.assertEqual(dict(self.usuario()), antes)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM autorizaciones_cambio").fetchone()[0], 0)

    def test_autorizacion_caduca(self):
        token = self.autorizar().autorizacion_cambio
        self.fecha += timedelta(minutes=5)
        with self.assertRaises(CambioContrasenaDenegado):
            self.cambiar(token)

    def test_nueva_invalida_o_igual_no_consume_autorizacion(self):
        token = self.autorizar().autorizacion_cambio
        for nueva in ("corta", self.actual):
            with self.assertRaises(DatosCuentaInvalidos):
                self.cambiar(token, nueva)
        self.cambiar(token)
        self.assertEqual(self.usuario()["version_credenciales"], 2)

    def test_bloqueo_desactivacion_y_espera_impiden_cambio(self):
        token = self.autorizar().autorizacion_cambio
        for columna, valor in (
            ("habilitado", 0), ("bloqueado_en", self.fecha.isoformat()),
            ("proximo_intento_en", (self.fecha + timedelta(seconds=60)).isoformat()),
        ):
            self.ejecutar("UPDATE usuarios SET habilitado=1, bloqueado_en=NULL, proximo_intento_en=NULL")
            self.ejecutar(f"UPDATE usuarios SET {columna}=?", (valor,))
            with self.subTest(columna=columna), self.assertRaises(CambioContrasenaDenegado):
                self.cambiar(token)

    def test_cambio_de_rol_correo_o_version_impide_cambio(self):
        token = self.autorizar().autorizacion_cambio
        for sql in (
            "UPDATE usuarios SET rol='conductor'",
            "UPDATE usuarios SET rol='administrador', version_credenciales=2",
            "UPDATE usuarios SET version_credenciales=1, cambio_obligatorio=0",
        ):
            self.ejecutar(sql)
            with self.assertRaises(CambioContrasenaDenegado):
                self.cambiar(token)
        self.ejecutar("UPDATE usuarios SET cambio_obligatorio=1")
        self.ejecutar("UPDATE configuracion_administrador SET correo_verificacion='otro@example.invalid'")
        with self.assertRaises(CambioContrasenaDenegado):
            self.cambiar(token)

    def test_fallo_de_guardado_revierte_hash_y_consumo(self):
        token = self.autorizar().autorizacion_cambio
        antes = dict(self.usuario())
        self.ejecutar("""CREATE TRIGGER impedir_consumo BEFORE UPDATE ON autorizaciones_cambio
                        BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cambiar(token)
        self.assertEqual(dict(self.usuario()), antes)
        self.ejecutar("DROP TRIGGER impedir_consumo")
        self.cambiar(token)

    def test_dos_cambios_simultaneos_solo_confirman_uno(self):
        token = self.autorizar().autorizacion_cambio
        barrera = Barrier(2)
        protector = PasswordHasher()
        def generar(clave):
            protegido = protector.hash(clave)
            barrera.wait(timeout=5)
            return protegido
        def intentar(clave):
            try:
                self.cambiar(token, clave)
                return clave
            except CambioContrasenaDenegado:
                return None
        with patch("taxiroute.cambio_administrador._PROTECTOR") as doble:
            doble.verify.side_effect = protector.verify
            doble.hash.side_effect = generar
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                resultados = list(ejecutor.map(intentar, [self.nueva, "OtraFicticia789!"]))
        ganadoras = [r for r in resultados if r is not None]
        self.assertEqual(len(ganadoras), 1)
        self.assertEqual(self.usuario()["version_credenciales"], 2)
        self.assertTrue(protector.verify(self.usuario()["hash_contrasena"], ganadoras[0]))

    def test_expiracion_durante_hash_no_modifica_cuenta(self):
        token = self.autorizar().autorizacion_cambio
        antes = dict(self.usuario())
        protector = PasswordHasher()
        def generar(clave):
            self.fecha += timedelta(minutes=5)
            return protector.hash(clave)
        with patch("taxiroute.cambio_administrador._PROTECTOR") as doble:
            doble.verify.side_effect = protector.verify
            doble.hash.side_effect = generar
            with self.assertRaises(CambioContrasenaDenegado):
                self.cambiar(token)
        self.assertEqual(dict(self.usuario()), antes)

    def test_pin_sin_cambio_obligatorio_no_emite_autorizacion_de_cambio(self):
        self.ejecutar("UPDATE usuarios SET cambio_obligatorio=0")
        self.assertIsNone(self.autorizar().autorizacion_cambio)


class PruebasMigracionAutorizacion(unittest.TestCase):
    def test_actualizacion_v2_y_reversion_en_error(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "version2.sqlite3"
            with conectar(ruta) as c:
                c.execute(CREAR_USUARIOS)
                for sentencia in MIGRACION_PIN:
                    c.execute(sentencia)
                c.execute(f"PRAGMA application_id={IDENTIFICADOR_APLICACION}")
                c.execute("PRAGMA user_version=2")
            crear_cuenta(ruta, nombre_usuario="Conductor", nombre_visible="Prueba",
                         contrasena="AnteriorFicticia123!")
            with patch("taxiroute.base_cuentas.MIGRACION_AUTORIZACION", "SQL INVALIDO"):
                with self.assertRaises(sqlite3.OperationalError):
                    inicializar_base(ruta)
            with conectar(ruta) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            inicializar_base(ruta)
            inicializar_base(ruta)
            with conectar(ruta) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM autorizaciones_cambio").fetchone()[0], 0)
