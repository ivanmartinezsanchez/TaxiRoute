"""Alta de cuentas en bases temporales, con contraseñas exclusivamente ficticias."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import (
    DatosCuentaInvalidos, UsuarioDuplicado, crear_cuenta,
    normalizar_usuario, validar_contrasena,
)

CLAVE_PRUEBA = "PruebaLarga123!"


class PruebasCuentas(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)

    def alta(self, nombre="Ana", **cambios):
        datos = dict(nombre_usuario=nombre, nombre_visible="Ana de prueba",
                     contrasena=CLAVE_PRUEBA)
        datos.update(cambios)
        return crear_cuenta(self.ruta, **datos)

    def filas(self):
        with conectar(self.ruta) as conexion:
            return conexion.execute("SELECT * FROM usuarios").fetchall()

    def test_alta_persistente_sin_secretos_en_resultado(self):
        cuenta = self.alta()
        fila = self.filas()[0]
        self.assertEqual(cuenta.id, fila["id"])
        self.assertEqual(fila["nombre_usuario_normalizado"], "ana")
        self.assertEqual(fila["rol"], "conductor")
        self.assertEqual(fila["cambio_obligatorio"], 1)
        self.assertIsNone(fila["ultimo_acceso_en"])
        self.assertNotIn(CLAVE_PRUEBA, repr(cuenta))
        self.assertFalse(hasattr(cuenta, "hash_contrasena"))
        self.assertTrue(fila["hash_contrasena"].startswith("$argon2id$"))
        self.assertTrue(PasswordHasher().verify(fila["hash_contrasena"], CLAVE_PRUEBA))
        with self.assertRaises(VerifyMismatchError):
            PasswordHasher().verify(fila["hash_contrasena"], "Incorrecta123!")
        self.assertNotIn(CLAVE_PRUEBA.encode(), self.ruta.read_bytes())

    def test_misma_clave_distinto_salt(self):
        self.alta("Ana")
        self.alta("Bea")
        filas = self.filas()
        self.assertNotEqual(filas[0]["hash_contrasena"], filas[1]["hash_contrasena"])
        self.assertNotEqual(filas[0]["id"], filas[1]["id"])

    def test_duplicado_por_mayusculas_y_unicode_conserva_original(self):
        self.alta("Iván")
        for nombre in ("IVÁN", "Iva\u0301n"):
            with self.subTest(nombre=nombre), self.assertRaises(UsuarioDuplicado):
                self.alta(nombre)
        self.assertEqual(len(self.filas()), 1)

    def test_datos_invalidos_no_crean_filas(self):
        for cambios in (
            {"nombre_usuario": "ab"}, {"nombre_usuario": "ana admin"},
            {"nombre_usuario": "ana'; DROP TABLE usuarios;--"},
            {"nombre_usuario": None}, {"nombre_visible": " "},
            {"rol": "superusuario"}, {"contrasena": "Corta123!"},
        ):
            with self.subTest(cambios=list(cambios)), self.assertRaises(DatosCuentaInvalidos):
                self.alta(**cambios)
        self.assertEqual(self.filas(), [])

    def test_politica_composicion_limites_y_control(self):
        for clave in (
            "solominusculas123!", "SOLOMAYUSCULAS123!", "AbcXXXXXXXX123!",
            "PruebaLarga12!", "PruebaLarga123 ", "Aabc123!" + "x" * 121,
            "PruebaLarga123!\n",
        ):
            # Abc solo tiene dos minúsculas.
            with self.subTest(longitud=len(clave)), self.assertRaises(DatosCuentaInvalidos):
                validar_contrasena(clave)
        validar_contrasena("Aabc123!" + "x" * 120)
        validar_contrasena(" PruebaLarga123! ")

    def test_espacios_de_contrasena_se_conservan(self):
        clave = " PruebaLarga123! "
        self.alta(contrasena=clave)
        protegido = self.filas()[0]["hash_contrasena"]
        self.assertTrue(PasswordHasher().verify(protegido, clave))
        with self.assertRaises(VerifyMismatchError):
            PasswordHasher().verify(protegido, clave.strip())

    def test_fallo_hash_no_escribe(self):
        with patch("taxiroute.cuentas._PROTECTOR") as protector:
            protector.hash.side_effect = RuntimeError("fallo simulado")
            with self.assertRaises(RuntimeError):
                self.alta()
        self.assertEqual(self.filas(), [])

    def test_altas_concurrentes_mismo_usuario_solo_crean_una_cuenta(self):
        def intentar(nombre):
            try:
                self.alta(nombre)
                return "creada"
            except UsuarioDuplicado:
                return "duplicada"
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, ("Ana", "ANA")))
        self.assertCountEqual(resultados, ["creada", "duplicada"])
        self.assertEqual(len(self.filas()), 1)
