"""Pruebas aisladas con cuentas y secretos ficticios."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from taxiroute.acceso import PasoAcceso, comprobar_credenciales, cambiar_contrasena
from taxiroute.base_cuentas import conectar, inicializar_base, MIGRACION_AUDITORIA_V7
from taxiroute.cuentas import _PROTECTOR, crear_cuenta, DatosCuentaInvalidos
from taxiroute.pin import ServicioPIN
from taxiroute.restablecimiento_conductor import restablecer_contrasena_conductor, RestablecimientoDenegado
from taxiroute.sesiones import ServicioSesiones


class PruebasRestablecimiento(unittest.TestCase):
    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.ruta = Path(carpeta.name) / 'cuentas.sqlite3'
        inicializar_base(self.ruta)
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.clave = 'FicticiaLarga123!'
        self.nueva = 'TemporalConductor123!'
        admin = crear_cuenta(self.ruta, nombre_usuario='Admin', nombre_visible='Admin', contrasena=self.clave, rol='administrador')
        conductor = crear_cuenta(self.ruta, nombre_usuario='Ana', nombre_visible='Ana', contrasena=self.clave)
        self.admin, self.conductor = admin.id, conductor.id
        with conectar(self.ruta) as c:
            c.execute('UPDATE usuarios SET cambio_obligatorio=0')
            c.execute('INSERT INTO configuracion_administrador VALUES (?, ?)', (self.admin, 'demo@example.invalid'))
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), enviar=lambda correo, codigo: buzon.append(codigo), reloj=lambda: self.fecha)
        desafio = pin.solicitar('Admin', self.clave)
        self.token = pin.verificar(desafio, buzon[-1]).sesion.token
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token_conductor = self.sesiones.iniciar_conductor('Ana', self.clave).token

    def operar(self, token=None, usuario=None, nueva=None):
        restablecer_contrasena_conductor(self.ruta, self.token if token is None else token,
            self.conductor if usuario is None else usuario, self.nueva if nueva is None else nueva,
            reloj=lambda: self.fecha)

    def estado(self):
        with conectar(self.ruta) as c:
            return (dict(c.execute('SELECT * FROM usuarios WHERE id=?', (self.conductor,)).fetchone()),
                    [dict(f) for f in c.execute('SELECT * FROM auditoria_cuentas')])

    def test_cambia_hash_obliga_cambio_revoca_y_audita(self):
        antes, _ = self.estado()
        self.operar()
        despues, auditoria = self.estado()
        self.assertNotEqual(despues['hash_contrasena'], self.nueva)
        self.assertTrue(_PROTECTOR.verify(despues['hash_contrasena'], self.nueva))
        self.assertEqual(despues['version_credenciales'], antes['version_credenciales'] + 1)
        self.assertEqual(despues['cambio_obligatorio'], 1)
        self.assertIsNone(self.sesiones.consultar(self.token_conductor))
        self.assertEqual([(x['actor_id'], x['afectado_id'], x['accion']) for x in auditoria],
                         [(self.admin, self.conductor, 'restablecer_contrasena')])
        self.assertEqual(comprobar_credenciales(self.ruta, 'Ana', self.nueva, reloj=lambda: self.fecha).paso, PasoAcceso.CAMBIO_PENDIENTE)
        self.assertIsNone(self.sesiones.iniciar_conductor('Ana', self.nueva))
        cambiar_contrasena(self.ruta, 'Ana', self.nueva, 'ClaveDefinitiva123!', reloj=lambda: self.fecha)
        self.assertIsNotNone(self.sesiones.iniciar_conductor('Ana', 'ClaveDefinitiva123!'))

    def test_denegaciones_no_modifican_datos(self):
        antes = self.estado()
        for token, destino in ((self.token_conductor, self.conductor), ('x' * 43, self.conductor),
                               (self.token, self.admin), (self.token, 'inexistente')):
            with self.assertRaises(RestablecimientoDenegado):
                self.operar(token=token, usuario=destino)
        with self.assertRaises(DatosCuentaInvalidos):
            self.operar(nueva='facil')
        self.assertEqual(antes, self.estado())
        self.assertIsNotNone(self.sesiones.consultar(self.token_conductor))

    def test_bloqueo_y_deshabilitacion_se_conservan(self):
        with conectar(self.ruta) as c:
            c.execute('UPDATE usuarios SET habilitado=0, bloqueado_en=?, intentos_fallidos=15 WHERE id=?', (self.fecha.isoformat(), self.conductor))
        self.operar()
        fila, _ = self.estado()
        self.assertEqual((fila['habilitado'], fila['intentos_fallidos']), (0, 15))
        self.assertIsNotNone(fila['bloqueado_en'])

    def test_si_falla_auditoria_se_revierte_todo(self):
        antes = self.estado()
        with conectar(self.ruta) as c:
            c.execute("CREATE TRIGGER falla BEFORE INSERT ON auditoria_cuentas BEGIN SELECT RAISE(ABORT, 'simulado'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.operar()
        self.assertEqual(antes, self.estado())
        self.assertIsNotNone(self.sesiones.consultar(self.token_conductor))

    def test_revocacion_durante_hash_impide_cambio(self):
        antes = self.estado()
        hash_real = _PROTECTOR.hash
        def revocar_y_hashear(clave):
            resultado = hash_real(clave)
            self.sesiones.cerrar(self.token)
            return resultado
        with patch('taxiroute.restablecimiento_conductor._PROTECTOR') as protector:
            protector.hash.side_effect = revocar_y_hashear
            with self.assertRaises(RestablecimientoDenegado):
                self.operar()
        self.assertEqual(antes, self.estado())

    def test_caducidad_denegada(self):
        antes = self.estado()
        self.fecha += timedelta(hours=1)
        with self.assertRaises(RestablecimientoDenegado):
            self.operar()
        self.assertEqual(antes, self.estado())

    def test_migracion_v7_conserva_auditoria(self):
        with conectar(self.ruta) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("DROP TABLE recibos_operacion")
            c.execute("DROP TABLE intenciones_operacion")
            for sentencia in MIGRACION_AUDITORIA_V7:
                c.execute(sentencia)
            c.execute("INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha) VALUES (?, ?, 'alta', ?)", (self.admin, self.conductor, self.fecha.isoformat()))
            c.execute('DROP TABLE codigos_recuperacion')
            c.execute("DROP TABLE revisiones_csv_ausente")
            c.execute("DROP TABLE procedencia_sqlite")
            c.execute("DROP TABLE intenciones_descartadas")
            c.execute("DROP TABLE barrera_revision")
            c.execute("DROP TABLE vinculo_historial")
            c.execute('PRAGMA user_version=7')
            c.commit()
        inicializar_base(self.ruta)
        self.operar()
        _, auditoria = self.estado()
        self.assertEqual([x['accion'] for x in auditoria], ['alta', 'restablecer_contrasena'])
