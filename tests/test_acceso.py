"""Pruebas de credenciales sin sesiones reales, correo ni datos de uso."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher

from taxiroute.acceso import PasoAcceso, comprobar_credenciales
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta


class PruebasAcceso(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "FicticiaLarga123!"
        self.fecha = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                     contrasena=self.clave)

    def comprobar(self, clave=None, nombre="Ana"):
        return comprobar_credenciales(self.ruta, nombre,
            self.clave if clave is None else clave, reloj=lambda: self.fecha)

    def fila(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM usuarios WHERE nombre_usuario='Ana'").fetchone()

    def actualizar(self, sql, valores=()):
        with conectar(self.ruta) as c:
            c.execute(sql, valores)

    def test_temporal_no_concede_acceso_ni_borra_fallos(self):
        self.comprobar("incorrecta")
        self.fecha += timedelta(seconds=61)
        resultado = self.comprobar()
        self.assertEqual(resultado.paso, PasoAcceso.CAMBIO_PENDIENTE)
        self.assertEqual(self.fila()["intentos_fallidos"], 1)
        self.assertIsNone(self.fila()["ultimo_acceso_en"])

    def test_administrador_requiere_pin_incluso_con_clave_temporal(self):
        self.actualizar("UPDATE usuarios SET rol='administrador'")
        self.assertEqual(self.comprobar().paso, PasoAcceso.PIN_PENDIENTE)
        self.assertIsNone(self.fila()["ultimo_acceso_en"])

    def test_conductor_verificado_no_es_una_sesion(self):
        self.actualizar("UPDATE usuarios SET cambio_obligatorio=0")
        r = self.comprobar(nombre="ANA")
        self.assertEqual(r.paso, PasoAcceso.CONTRASENA_VERIFICADA)
        self.assertEqual(r.usuario_id, self.fila()["id"])
        self.assertIsNone(self.fila()["ultimo_acceso_en"])

    def test_quince_fallos_bloquean_y_persisten_al_reabrir(self):
        for numero in range(1, 16):
            self.assertEqual(self.comprobar("incorrecta").paso, PasoAcceso.DENEGADO)
            self.assertEqual(self.fila()["intentos_fallidos"], numero)
            self.fecha += timedelta(seconds=61)
        inicializar_base(self.ruta)
        self.fecha += timedelta(days=1)
        self.assertIsNotNone(self.fila()["bloqueado_en"])
        self.assertEqual(self.comprobar().paso, PasoAcceso.DENEGADO)
        self.assertEqual(self.fila()["intentos_fallidos"], 15)

    def test_espera_no_admite_otro_intento(self):
        self.comprobar("incorrecta")
        self.assertEqual(self.comprobar().paso, PasoAcceso.DENEGADO)
        self.assertEqual(self.fila()["intentos_fallidos"], 1)
        self.fecha += timedelta(seconds=1)
        self.assertEqual(self.comprobar().paso, PasoAcceso.CAMBIO_PENDIENTE)

    def test_desactivada_inexistente_y_hash_corrupto_denegados(self):
        self.assertEqual(self.comprobar(nombre="NoExiste").paso, PasoAcceso.DENEGADO)
        self.actualizar("UPDATE usuarios SET habilitado=0")
        self.assertEqual(self.comprobar().paso, PasoAcceso.DENEGADO)
        self.actualizar("UPDATE usuarios SET habilitado=1, hash_contrasena='corrupto'")
        self.assertEqual(self.comprobar().paso, PasoAcceso.DENEGADO)

    def test_fallos_simultaneos_respetan_la_espera(self):
        barrera = Barrier(2)
        protector = PasswordHasher()
        def verificar(protegido, clave):
            barrera.wait(timeout=5)
            return protector.verify(protegido, clave)
        with patch("taxiroute.acceso._PROTECTOR") as doble:
            doble.verify.side_effect = verificar
            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                resultados = list(ejecutor.map(self.comprobar, ["incorrecta", "incorrecta"]))
        self.assertTrue(all(r.paso == PasoAcceso.DENEGADO for r in resultados))
        self.assertEqual(self.fila()["intentos_fallidos"], 1)
        self.assertEqual(
            self.fila()["proximo_intento_en"],
            (self.fecha + timedelta(seconds=1)).isoformat(),
        )
        self.fecha += timedelta(seconds=1)
        self.assertEqual(self.comprobar("incorrecta").paso, PasoAcceso.DENEGADO)
        self.assertEqual(self.fila()["intentos_fallidos"], 2)

    def test_cambio_de_credenciales_durante_verificacion_rechazado(self):
        protector = PasswordHasher()
        def verificar(protegido, clave):
            correcto = protector.verify(protegido, clave)
            self.actualizar("UPDATE usuarios SET version_credenciales=version_credenciales+1")
            return correcto
        with patch("taxiroute.acceso._PROTECTOR") as doble:
            doble.verify.side_effect = verificar
            self.assertEqual(self.comprobar().paso, PasoAcceso.DENEGADO)

    def test_entrada_invalida_no_crea_cuentas(self):
        for valor in (None, "x" * 129, "\ud800"):
            r = comprobar_credenciales(self.ruta, "Ana", valor, reloj=lambda: self.fecha)
            self.assertEqual(r.paso, PasoAcceso.DENEGADO)
        self.assertEqual(self.fila()["intentos_fallidos"], 0)
