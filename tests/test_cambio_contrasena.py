"""Cambio de contraseñas ficticias en bases temporales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from taxiroute.acceso import cambiar_contrasena, CambioContrasenaDenegado
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta, DatosCuentaInvalidos


class PruebasCambioContrasena(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.actual = "ActualFicticia123!"
        self.nueva = "NuevaFicticia456!"
        self.fecha = datetime(2026, 11, 8, 14, tzinfo=timezone.utc)
        crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                     contrasena=self.actual)

    def cambiar(self, actual=None, nueva=None):
        return cambiar_contrasena(self.ruta, "Ana",
            self.actual if actual is None else actual,
            self.nueva if nueva is None else nueva, reloj=lambda: self.fecha)

    def fila(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM usuarios").fetchone()

    def actualizar(self, sql, parametros=()):
        with conectar(self.ruta) as c:
            c.execute(sql, parametros)

    def test_cambio_sustituye_hash_y_marca_sin_crear_sesion(self):
        antes = self.fila()
        self.cambiar()
        fila = self.fila()
        self.assertTrue(PasswordHasher().verify(fila["hash_contrasena"], self.nueva))
        with self.assertRaises(VerifyMismatchError):
            PasswordHasher().verify(fila["hash_contrasena"], self.actual)
        self.assertEqual(fila["version_credenciales"], antes["version_credenciales"] + 1)
        self.assertEqual(fila["cambio_obligatorio"], 0)
        self.assertEqual(fila["id"], antes["id"])
        self.assertIsNone(fila["ultimo_acceso_en"])

    def test_actual_incorrecta_cuenta_como_fallo(self):
        antes = self.fila()["hash_contrasena"]
        with self.assertRaises(CambioContrasenaDenegado):
            self.cambiar(actual="incorrecta")
        self.assertEqual(self.fila()["intentos_fallidos"], 1)
        self.assertEqual(self.fila()["hash_contrasena"], antes)

    def test_nueva_invalida_o_igual_no_modifica_la_cuenta(self):
        antes = dict(self.fila())
        for nueva in ("corta", self.actual, None):
            with self.subTest(nueva=nueva), self.assertRaises(DatosCuentaInvalidos):
                cambiar_contrasena(self.ruta, "Ana", self.actual, nueva,
                                   reloj=lambda: self.fecha)
            self.assertEqual(dict(self.fila()), antes)

    def test_bloqueada_desactivada_y_en_espera_no_permiten_cambio(self):
        for campo, valor in (
            ("bloqueado_en", self.fecha.isoformat()),
            ("habilitado", 0),
            ("proximo_intento_en", (self.fecha + timedelta(seconds=60)).isoformat()),
        ):
            self.actualizar("UPDATE usuarios SET bloqueado_en=NULL, habilitado=1, proximo_intento_en=NULL")
            self.actualizar(f"UPDATE usuarios SET {campo}=?", (valor,))
            antes = dict(self.fila())
            with self.subTest(campo=campo), self.assertRaises(CambioContrasenaDenegado):
                self.cambiar()
            self.assertEqual(dict(self.fila()), antes)

    def test_administrador_no_puede_omitir_pin(self):
        self.actualizar("UPDATE usuarios SET rol='administrador'")
        antes = dict(self.fila())
        with self.assertRaises(CambioContrasenaDenegado):
            self.cambiar()
        self.assertEqual(dict(self.fila()), antes)

    def test_fallos_previos_no_se_borran(self):
        self.actualizar("UPDATE usuarios SET intentos_fallidos=3")
        self.cambiar()
        self.assertEqual(self.fila()["intentos_fallidos"], 3)

    def test_fallo_hash_conserva_credenciales(self):
        protector = PasswordHasher()
        antes = dict(self.fila())
        with patch("taxiroute.acceso._PROTECTOR") as doble:
            doble.verify.side_effect = protector.verify
            doble.hash.side_effect = RuntimeError("simulado")
            with self.assertRaises(RuntimeError):
                self.cambiar()
        self.assertEqual(dict(self.fila()), antes)

    def test_desactivacion_durante_hash_impide_guardado(self):
        protector = PasswordHasher()
        antes = self.fila()["hash_contrasena"]
        def generar(clave):
            self.actualizar("UPDATE usuarios SET habilitado=0")
            return protector.hash(clave)
        with patch("taxiroute.acceso._PROTECTOR") as doble:
            doble.verify.side_effect = protector.verify
            doble.hash.side_effect = generar
            with self.assertRaises(CambioContrasenaDenegado):
                self.cambiar()
        self.assertEqual(self.fila()["hash_contrasena"], antes)
        self.assertEqual(self.fila()["habilitado"], 0)

    def test_dos_cambios_simultaneos_solo_confirman_uno(self):
        protector = PasswordHasher()
        barrera = Barrier(2)
        def generar(clave):
            protegido = protector.hash(clave)
            barrera.wait(timeout=5)
            return protegido
        def intentar(clave):
            try:
                self.cambiar(nueva=clave)
                return clave
            except CambioContrasenaDenegado:
                return None
        with patch("taxiroute.acceso._PROTECTOR") as doble:
            doble.verify.side_effect = protector.verify
            doble.hash.side_effect = generar
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                resultados = list(ejecutor.map(intentar, [self.nueva, "OtraFicticia789!"]))
        ganadoras = [r for r in resultados if r is not None]
        self.assertEqual(len(ganadoras), 1)
        self.assertEqual(self.fila()["version_credenciales"], 2)
        self.assertTrue(protector.verify(self.fila()["hash_contrasena"], ganadoras[0]))
