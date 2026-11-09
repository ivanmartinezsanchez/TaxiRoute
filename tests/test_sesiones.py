"""Sesiones con reloj controlado, bases temporales y correo en memoria."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.acceso import cambiar_contrasena
from taxiroute.base_cuentas import (
    conectar, inicializar_base, CREAR_USUARIOS, MIGRACION_PIN,
    MIGRACION_AUTORIZACION, IDENTIFICADOR_APLICACION, VERSION_ESQUEMA,
)
from taxiroute.carrera import Carrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasSesiones(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.fecha = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
        self.clave = "FicticiaLarga123!"
        crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                     contrasena=self.clave)
        self.sql("UPDATE usuarios SET cambio_obligatorio=0")
        self.servicio = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)

    def sql(self, sentencia, datos=()):
        with conectar(self.ruta) as c:
            c.execute(sentencia, datos)

    def fila(self):
        with conectar(self.ruta) as c:
            return c.execute("SELECT * FROM usuarios").fetchone()

    def iniciar(self):
        return self.servicio.iniciar_conductor("Ana", self.clave)

    def preparar_admin(self):
        self.sql("UPDATE usuarios SET rol='administrador'")
        self.sql("INSERT INTO configuracion_administrador VALUES (?, ?)",
                 (self.fila()["id"], "pruebas@example.invalid"))
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
                         enviar=lambda correo, codigo: buzon.append(codigo),
                         reloj=lambda: self.fecha)
        return pin, buzon

    def test_creacion_solo_hash_y_reinicio_de_fallos_al_completar(self):
        self.sql("UPDATE usuarios SET intentos_fallidos=3")
        sesion = self.iniciar()
        self.assertNotIn(sesion.token, repr(sesion))
        with conectar(self.ruta) as c:
            almacenada = dict(c.execute("SELECT * FROM sesiones").fetchone())
        self.assertNotIn(sesion.token, str(almacenada))
        self.assertEqual(self.fila()["intentos_fallidos"], 0)
        self.assertEqual(self.fila()["ultimo_acceso_en"], self.fecha.isoformat())
        self.assertEqual(self.servicio.consultar(sesion.token).usuario_id, self.fila()["id"])

    def test_temporal_y_administrador_no_acceden_con_solo_password(self):
        self.sql("UPDATE usuarios SET cambio_obligatorio=1")
        self.assertIsNone(self.iniciar())
        self.sql("UPDATE usuarios SET cambio_obligatorio=0, rol='administrador'")
        self.assertIsNone(self.iniciar())

    def test_inactividad_exacta_y_consultas_automaticas_no_renuevan(self):
        token = self.iniciar().token
        self.fecha += timedelta(minutes=59, seconds=59)
        self.assertIsNotNone(self.servicio.consultar(token))
        self.fecha += timedelta(seconds=1)
        self.assertIsNone(self.servicio.consultar(token, interaccion=True))
        self.assertIsNone(self.servicio.consultar(token))

    def test_actividad_renueva_inactividad_pero_no_ocho_horas(self):
        token = self.iniciar().token
        for _ in range(15):
            self.fecha += timedelta(minutes=30)
            self.assertIsNotNone(self.servicio.consultar(token, interaccion=True))
        self.fecha += timedelta(minutes=30)
        self.assertIsNone(self.servicio.consultar(token, interaccion=True))

    def test_cierre_no_detiene_carrera_ni_otra_sesion(self):
        primera = self.iniciar().token
        segunda = self.iniciar().token
        tiempo = [0]
        carreras = ServicioCarreras(lambda: Carrera(reloj=lambda: tiempo[0]))
        carreras.ejecutar("iniciar")
        self.servicio.cerrar(primera)
        self.servicio.cerrar(primera)
        self.assertIsNone(self.servicio.consultar(primera))
        self.assertIsNotNone(self.servicio.consultar(segunda))
        tiempo[0] = 10_000_000_000
        self.assertIsNotNone(carreras.carrera)
        self.assertEqual(str(carreras.carrera.importe_actual()), "0.20")
        self.fecha += timedelta(hours=1)
        self.assertIsNone(self.servicio.consultar(segunda))
        self.assertIsNotNone(carreras.carrera)

    def test_cambio_contrasena_invalida_sesion_anterior(self):
        token = self.iniciar().token
        cambiar_contrasena(self.ruta, "Ana", self.clave, "OtraFicticia456!",
                           reloj=lambda: self.fecha)
        self.assertIsNone(self.servicio.consultar(token))

    def test_cuenta_desactivada_bloqueada_o_rol_cambiado_invalida(self):
        for columna, valor in (
            ("habilitado", 0), ("bloqueado_en", self.fecha.isoformat()),
            ("rol", "administrador"), ("cambio_obligatorio", 1),
        ):
            self.sql("UPDATE usuarios SET habilitado=1, bloqueado_en=NULL, rol='conductor', cambio_obligatorio=0")
            token = self.iniciar().token
            self.sql(f"UPDATE usuarios SET {columna}=?", (valor,))
            self.assertIsNone(self.servicio.consultar(token))
            self.sql("UPDATE usuarios SET habilitado=1, bloqueado_en=NULL, rol='conductor', cambio_obligatorio=0")
            self.assertIsNone(self.servicio.consultar(token))

    def test_sesion_persistente_y_tokens_inventados(self):
        token = self.iniciar().token
        nueva = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.assertIsNotNone(nueva.consultar(token))
        for falso in (None, True, "x" * 43, {"usuario": "Ana"}, "é" * 43):
            self.assertIsNone(nueva.consultar(falso))
            nueva.cerrar(falso)

    def test_admin_solo_obtiene_sesion_despues_de_pin(self):
        pin, buzon = self.preparar_admin()
        self.sql("UPDATE usuarios SET intentos_fallidos=2")
        desafio = pin.solicitar("Ana", self.clave)
        self.assertEqual(self.fila()["intentos_fallidos"], 2)
        resultado = pin.verificar(desafio, buzon[-1])
        self.assertIsNotNone(resultado.sesion)
        self.assertIsNone(resultado.autorizacion_cambio)
        self.assertEqual(self.fila()["intentos_fallidos"], 0)
        self.assertIsNotNone(self.servicio.consultar(resultado.sesion.token))
        self.assertIsNone(pin.verificar(desafio, buzon[-1]))
        self.sql("UPDATE configuracion_administrador SET correo_verificacion='otro@example.invalid'")
        self.assertIsNone(self.servicio.consultar(resultado.sesion.token))

    def test_admin_temporal_solo_recibe_autorizacion_de_cambio(self):
        pin, buzon = self.preparar_admin()
        self.sql("UPDATE usuarios SET cambio_obligatorio=1, intentos_fallidos=2")
        desafio = pin.solicitar("Ana", self.clave)
        resultado = pin.verificar(desafio, buzon[-1])
        self.assertIsNone(resultado.sesion)
        self.assertIsNotNone(resultado.autorizacion_cambio)
        self.assertEqual(self.fila()["intentos_fallidos"], 2)

    def test_fallo_creacion_sesion_no_consume_pin_ni_borra_fallos(self):
        pin, buzon = self.preparar_admin()
        self.sql("UPDATE usuarios SET intentos_fallidos=2")
        desafio = pin.solicitar("Ana", self.clave)
        self.sql("""CREATE TRIGGER fallo_sesion BEFORE INSERT ON sesiones
                    BEGIN SELECT RAISE(ABORT, 'simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            pin.verificar(desafio, buzon[-1])
        self.assertEqual(self.fila()["intentos_fallidos"], 2)
        self.assertIsNone(self.fila()["ultimo_acceso_en"])
        self.sql("DROP TRIGGER fallo_sesion")
        self.assertIsNotNone(pin.verificar(desafio, buzon[-1]).sesion)

    def test_retroceso_reloj_no_extiende_sesion(self):
        token = self.iniciar().token
        self.fecha -= timedelta(seconds=1)
        self.assertIsNone(self.servicio.consultar(token, interaccion=True))


class PruebasMigracionSesiones(unittest.TestCase):
    def test_migracion_desde_v3_conserva_usuarios_y_revierte_fallo(self):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "cuentas.sqlite3"
            with conectar(ruta) as c:
                c.execute(CREAR_USUARIOS)
                for sentencia in MIGRACION_PIN:
                    c.execute(sentencia)
                c.execute(MIGRACION_AUTORIZACION)
                c.execute(f"PRAGMA application_id={IDENTIFICADOR_APLICACION}")
                c.execute("PRAGMA user_version=3")
            crear_cuenta(ruta, nombre_usuario="Ana", nombre_visible="Prueba",
                         contrasena="TemporalFicticia123!")
            with patch("taxiroute.base_cuentas.MIGRACION_SESIONES",
                       ("CREATE TABLE parcial(id INTEGER)", "SQL INVALIDO")):
                with self.assertRaises(sqlite3.OperationalError):
                    inicializar_base(ruta)
            with conectar(ruta) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='parcial'").fetchone())
            inicializar_base(ruta)
            inicializar_base(ruta)
            with conectar(ruta) as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], VERSION_ESQUEMA)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0], 0)
