"""PIN administrativo con buzón en memoria; nunca se envían correos reales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from taxiroute.acceso import comprobar_credenciales
from taxiroute.base_cuentas import (
    conectar, inicializar_base, CREAR_USUARIOS, IDENTIFICADOR_APLICACION, VERSION_ESQUEMA,
)
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN


class PruebasPIN(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.fecha = datetime(2026, 11, 8, 16, tzinfo=timezone.utc)
        self.clave = "AdministradorFicticio123!"
        cuenta = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Prueba",
                              contrasena=self.clave, rol="administrador")
        self.usuario_id = cuenta.id
        self.ejecutar("INSERT INTO configuracion_administrador VALUES (?, ?)",
                      (cuenta.id, "pruebas@example.invalid"))
        self.buzon = []
        self.secreto = token_bytes(32)
        self.servicio = self.nuevo_servicio()

    def nuevo_servicio(self, enviar=None, secreto=None):
        return ServicioPIN(self.ruta,
            clave_hmac=self.secreto if secreto is None else secreto,
            enviar=self.recibir if enviar is None else enviar, reloj=lambda: self.fecha)

    def recibir(self, correo, codigo):
        self.buzon.append((correo, codigo))

    def ejecutar(self, sql, datos=()):
        with conectar(self.ruta) as c:
            c.execute(sql, datos)

    def usuario(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM usuarios WHERE id=?", (self.usuario_id,)).fetchone()

    def desafio(self, identificador):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM desafios_acceso WHERE id=?", (identificador,)).fetchone()

    def solicitar(self):
        return self.servicio.solicitar("Admin", self.clave)

    def test_siete_cifras_destino_configurado_y_uso_unico(self):
        identificador = self.solicitar()
        correo, codigo = self.buzon[-1]
        self.assertEqual(correo, "pruebas@example.invalid")
        self.assertRegex(codigo, r"^[1-9][0-9]{6}$")
        self.assertIsNotNone(identificador)
        fila = self.desafio(identificador)
        self.assertEqual(fila["estado_envio"], "enviado")
        self.assertNotEqual(fila["verificador_pin"], codigo)
        resultado = self.servicio.verificar(identificador, codigo)
        self.assertEqual(resultado.usuario_id, self.usuario_id)
        self.assertTrue(resultado.cambio_obligatorio)
        self.assertIsNone(self.usuario()["ultimo_acceso_en"])
        self.assertIsNone(self.servicio.verificar(identificador, codigo))

    def test_no_envia_sin_password_correcta_ni_para_conductor(self):
        self.assertIsNone(self.servicio.solicitar("Admin", "incorrecta"))
        self.assertEqual(self.usuario()["intentos_fallidos"], 1)
        crear_cuenta(self.ruta, nombre_usuario="Conductor", nombre_visible="Prueba",
                     contrasena=self.clave)
        self.assertIsNone(self.servicio.solicitar("Conductor", self.clave))
        self.assertIsNone(self.servicio.solicitar("NoExiste", self.clave))
        self.assertEqual(self.buzon, [])

    def test_caduca_exactamente_a_los_cinco_minutos(self):
        identificador = self.solicitar()
        self.fecha += timedelta(minutes=5)
        self.assertIsNone(self.servicio.verificar(identificador, self.buzon[-1][1]))
        self.assertIsNone(self.desafio(identificador)["consumido_en"])

    def test_reenvio_espera_invalida_anterior_y_limite_persistente(self):
        primero = self.solicitar()
        anterior = self.buzon[-1][1]
        self.assertIsNone(self.solicitar())
        self.fecha += timedelta(minutes=1)
        segundo = self.solicitar()
        self.assertIsNotNone(segundo)
        self.assertIsNone(self.servicio.verificar(primero, anterior))
        self.fecha += timedelta(minutes=1)
        self.assertIsNotNone(self.solicitar())
        self.fecha += timedelta(minutes=1)
        servicio = self.nuevo_servicio()
        self.assertIsNone(servicio.solicitar("Admin", self.clave))
        self.assertEqual(len(self.buzon), 3)
        self.fecha += timedelta(minutes=12)
        self.assertIsNotNone(servicio.solicitar("Admin", self.clave))

    def test_envio_fallido_no_valida_codigo_ni_elimina_espera(self):
        recibidos = []
        def fallo(correo, codigo):
            recibidos.append(codigo)
            raise OSError("fallo simulado")
        servicio = self.nuevo_servicio(enviar=fallo)
        self.assertIsNone(servicio.solicitar("Admin", self.clave))
        with conectar(self.ruta) as c:
            fila = c.execute("SELECT * FROM desafios_acceso").fetchone()
        self.assertEqual(fila["estado_envio"], "fallido")
        self.assertIsNone(servicio.verificar(fila["id"], recibidos[0]))
        self.assertIsNone(self.solicitar())

    def test_pendiente_no_se_puede_consumir_durante_envio(self):
        resultados = []
        def transporte(correo, codigo):
            with conectar(self.ruta) as c:
                identificador = c.execute("SELECT id FROM desafios_acceso").fetchone()[0]
            resultados.append(self.servicio.verificar(identificador, codigo))
            self.recibir(correo, codigo)
        self.servicio = self.nuevo_servicio(enviar=transporte)
        identificador = self.solicitar()
        self.assertEqual(resultados, [None])
        self.assertIsNotNone(self.servicio.verificar(identificador, self.buzon[-1][1]))

    def test_fallos_comparten_contador_y_quince_bloquean(self):
        self.ejecutar("UPDATE usuarios SET intentos_fallidos=14")
        identificador = self.solicitar()
        self.assertIsNone(self.servicio.verificar(identificador, "0000000"))
        self.assertEqual(self.usuario()["intentos_fallidos"], 15)
        self.assertIsNotNone(self.usuario()["bloqueado_en"])
        self.fecha += timedelta(seconds=61)
        self.assertIsNone(self.nuevo_servicio().verificar(identificador, self.buzon[-1][1]))

    def test_exito_y_reenvio_no_borran_fallos(self):
        identificador = self.solicitar()
        self.servicio.verificar(identificador, "0000000")
        self.assertEqual(self.usuario()["intentos_fallidos"], 1)
        self.assertIsNone(self.servicio.verificar(identificador, self.buzon[-1][1]))
        self.fecha += timedelta(minutes=1)
        nuevo = self.solicitar()
        self.assertIsNotNone(self.servicio.verificar(nuevo, self.buzon[-1][1]))
        self.assertEqual(self.usuario()["intentos_fallidos"], 1)

    def test_clave_hmac_distinta_no_valida_y_reinicio_con_misma_si(self):
        identificador = self.solicitar()
        codigo = self.buzon[-1][1]
        self.assertIsNone(self.nuevo_servicio(secreto=token_bytes(32)).verificar(identificador, codigo))
        self.fecha += timedelta(seconds=1)
        self.assertIsNotNone(self.nuevo_servicio().verificar(identificador, codigo))

    def test_cambios_de_cuenta_invalidan_pin(self):
        identificador = self.solicitar()
        codigo = self.buzon[-1][1]
        for sql in (
            "UPDATE usuarios SET habilitado=0",
            "UPDATE usuarios SET habilitado=1, rol='conductor'",
            "UPDATE usuarios SET rol='administrador', version_credenciales=2",
        ):
            self.ejecutar(sql)
            self.assertIsNone(self.servicio.verificar(identificador, codigo))
        self.ejecutar("UPDATE usuarios SET version_credenciales=1")
        self.ejecutar("UPDATE configuracion_administrador SET correo_verificacion='otro@example.invalid'")
        self.assertIsNone(self.servicio.verificar(identificador, codigo))

    def test_cambio_durante_envio_no_activa_codigo(self):
        def transporte(correo, codigo):
            self.ejecutar("UPDATE usuarios SET habilitado=0")
            self.recibir(correo, codigo)
        self.assertIsNone(self.nuevo_servicio(enviar=transporte).solicitar("Admin", self.clave))

    def test_dos_consumos_simultaneos_solo_aceptan_uno(self):
        identificador = self.solicitar()
        codigo = self.buzon[-1][1]
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(
                lambda _: self.servicio.verificar(identificador, codigo), range(2)))
        self.assertEqual(sum(r is not None for r in resultados), 1)

    def test_dos_solicitudes_simultaneas_solo_envian_una(self):
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: self.solicitar(), range(2)))
        self.assertEqual(sum(r is not None for r in resultados), 1)
        self.assertEqual(len(self.buzon), 1)

    def test_formatos_invalidos_y_clave_hmac_insuficiente(self):
        with self.assertRaises(ValueError):
            self.nuevo_servicio(secreto=b"corta")
        identificador = self.solicitar()
        for codigo in (None, "１２３４５６７", "x" * 1000):
            self.assertIsNone(self.servicio.verificar(identificador, codigo))
            self.fecha += timedelta(seconds=61)
        self.assertEqual(self.usuario()["intentos_fallidos"], 3)
        self.assertIsNone(self.servicio.verificar("desconocido", "1234567"))


class PruebasMigracionPIN(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "anterior.sqlite3"
        with conectar(self.ruta) as c:
            c.execute(CREAR_USUARIOS)
            c.execute(f"PRAGMA application_id={IDENTIFICADOR_APLICACION}")
            c.execute("PRAGMA user_version=1")
        crear_cuenta(self.ruta, nombre_usuario="Anterior", nombre_visible="Prueba",
                     contrasena="TemporalFicticia123!")

    def test_migracion_conserva_todos_los_datos_y_referencias(self):
        with conectar(self.ruta) as c:
            antes = dict(c.execute("SELECT * FROM usuarios").fetchone())
        inicializar_base(self.ruta)
        inicializar_base(self.ruta)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
            self.assertEqual(dict(c.execute("SELECT * FROM usuarios").fetchone()), antes)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                          ("inexistente", "prueba@example.invalid"))

    def test_migracion_fallida_revierte_tablas_y_version(self):
        with patch("taxiroute.base_cuentas.MIGRACION_PIN",
                   ("CREATE TABLE temporal (id INTEGER)", "SQL INVALIDO")):
            with self.assertRaises(sqlite3.OperationalError):
                inicializar_base(self.ruta)
        with conectar(self.ruta) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='temporal'").fetchone())
            self.assertEqual(c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
        inicializar_base(self.ruta)
