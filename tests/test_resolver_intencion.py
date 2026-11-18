"""Resolución interna con cuentas y carreras temporales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria, OperacionNoPermitida
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.lectura_pendientes import huella_intencion, leer_estado_pendiente_en_transaccion
from taxiroute.pin import ServicioPIN
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.recuperacion_ultima_captura import cerrar_en_ultima_captura
from taxiroute.resolver_intencion import resolver_intencion_revisada
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasResolverIntencion(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        self.admin = crear_cuenta(self.base, nombre_usuario="Admin", nombre_visible="Admin", contrasena="AdminPrueba123!", rol="administrador")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (self.admin.id, "demo@example.invalid"))
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.ana_token = self.sesiones.iniciar_conductor("Ana", "Conductor123!").token
        self.luis_token = self.sesiones.iniciar_conductor("Luis", "Conductor123!").token
        buzon = []
        pin = ServicioPIN(self.base, clave_hmac=b"x" * 32, reloj=lambda: self.fecha,
                          enviar=lambda _, codigo: buzon.append(codigo))
        desafio = pin.solicitar("Admin", "AdminPrueba123!")
        self.admin_token = pin.verificar(desafio, buzon[-1]).sesion.token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(), usuarios_pendientes=[self.ana.id],
                                    modo_durable_experimental=True, revisiones_iniciales={self.ana.id: 0})
        self.id = str(uuid4())

    def iniciar_pendiente(self):
        registrar_intencion(self.sesiones, self.ana_token, id_intencion=self.id, accion="iniciar", revision=0)

    def huella(self):
        with conectar(self.base) as conexion:
            conexion.execute("BEGIN")
            punto, intencion = leer_estado_pendiente_en_transaccion(conexion, self.ana.id)
            resultado = huella_intencion(intencion, punto)
            conexion.rollback()
            return resultado

    def resolver(self, token, huella, confirmar=True):
        return resolver_intencion_revisada(self.sesiones, token, self.flota, self.ana.id,
                                            huella=huella, confirmar=confirmar)

    def test_inicio_exige_admin_y_protege_repeticiones(self):
        self.iniciar_pendiente()
        huella = self.huella()
        with self.assertRaises(SesionNoAutorizada):
            self.resolver(self.ana_token, huella)
        with self.assertRaises(OperacionNoPermitida):
            self.resolver(self.admin_token, huella, confirmar=False)
        self.assertEqual(self.resolver(self.admin_token, huella), "inicio_revisado")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.resolver(self.admin_token, huella), "inicio_revisado")
        with conectar(self.base) as conexion:
            acta = conexion.execute("SELECT * FROM intenciones_descartadas").fetchone()
            self.assertEqual((acta["usuario_id"], acta["actor_id"], acta["huella"]), (self.ana.id, self.admin.id, huella))
            self.assertEqual(conexion.execute("SELECT revision FROM barrera_revision").fetchone()[0], 1)
        for identificador in (self.id, str(uuid4())):
            with self.assertRaises(IntencionNoPermitida):
                registrar_intencion(self.sesiones, self.ana_token, id_intencion=identificador, accion="iniciar", revision=0)
        registrar_intencion(self.sesiones, self.ana_token, id_intencion=str(uuid4()), accion="iniciar", revision=1)
        with self.assertRaises(OperacionNoPermitida):
            self.resolver(self.admin_token, huella)

    def test_movimiento_revisa_y_cierra_ultimo_punto_sin_cobrar_laguna(self):
        inicio = self.fecha - timedelta(hours=4)
        punto = PuntoControl("viaje-1", self.ana.id, inicio, inicio + timedelta(seconds=10), "parado",
                             10_000_000_000, 0, "0.02", "0.05", 1)
        guardar_punto(self.base, punto)
        registrar_intencion(self.sesiones, self.ana_token, id_intencion=self.id, accion="mover", revision=1, carrera_id=punto.carrera_id)
        self.flota._revisiones[self.ana.id] = 1
        huella = self.huella()
        self.assertEqual(self.resolver(self.ana_token, huella), "carrera_pendiente")
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.resolver(self.ana_token, huella), "carrera_pendiente")
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT estado_registro FROM carreras_recuperacion").fetchone()[0], "revision_pendiente")
        resumen = cerrar_en_ultima_captura(self.sesiones, self.ana_token, self.flota,
                                            revision_esperada=1, ultimo_control_esperado=punto.ultimo_control)
        self.assertEqual(resumen.fin, punto.ultimo_control)
        self.assertEqual(str(resumen.importe), "0.20")

    def test_otro_conductor_revocacion_y_huella_incorrecta_no_cambian(self):
        self.iniciar_pendiente()
        huella = self.huella()
        with self.assertRaises(SesionNoAutorizada):
            self.resolver(self.luis_token, huella)
        with self.assertRaises(OperacionNoPermitida):
            self.resolver(self.admin_token, "0" * 64)
        self.sesiones.cerrar(self.admin_token)
        with self.assertRaises(SesionNoAutorizada):
            self.resolver(self.admin_token, huella)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_descartadas").fetchone()[0], 0)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_fallo_de_acta_revierte_todo_y_conserva_bloqueo(self):
        self.iniciar_pendiente()
        huella = self.huella()
        with conectar(self.base) as conexion:
            conexion.execute("CREATE TRIGGER falla_acta BEFORE INSERT ON intenciones_descartadas BEGIN SELECT RAISE(ABORT, 'simulado'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.resolver(self.admin_token, huella)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM barrera_revision").fetchone()[0], 0)
            conexion.execute("DROP TRIGGER falla_acta")
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.resolver(self.admin_token, huella), "inicio_revisado")

    def test_acta_ignorada_no_elimina_intencion(self):
        self.iniciar_pendiente()
        huella = self.huella()
        with conectar(self.base) as conexion:
            conexion.execute("CREATE TRIGGER ignora_acta BEFORE INSERT ON intenciones_descartadas BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(OperacionNoPermitida):
            self.resolver(self.admin_token, huella)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM barrera_revision").fetchone()[0], 0)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_dos_confirmaciones_simultaneas_no_duplican_acta(self):
        self.iniciar_pendiente()
        huella = self.huella()
        barrera = Barrier(2)
        def confirmar():
            barrera.wait(timeout=5)
            return self.resolver(self.admin_token, huella)
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: confirmar(), range(2)))
        self.assertEqual(resultados, ["inicio_revisado", "inicio_revisado"])
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_descartadas").fetchone()[0], 1)

    def test_error_despues_de_commit_conserva_bloqueo_y_permite_reintento(self):
        from taxiroute.conciliacion_csv import _conectar_existente
        self.iniciar_pendiente()
        huella = self.huella()
        class ConexionConFallo:
            def __init__(self, conexion): self.conexion = conexion
            def __getattr__(self, nombre): return getattr(self.conexion, nombre)
            def commit(self):
                self.conexion.commit()
                raise OSError("resultado incierto simulado")
        with patch("taxiroute.resolver_intencion._conectar_existente", side_effect=lambda ruta: ConexionConFallo(_conectar_existente(ruta))):
            with self.assertRaises(OSError):
                self.resolver(self.admin_token, huella)
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_descartadas").fetchone()[0], 1)
        self.assertEqual(self.resolver(self.admin_token, huella), "inicio_revisado")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))

    def test_sesion_caduca_al_esperar_bloqueo_no_resuelve(self):
        from contextlib import contextmanager
        self.iniciar_pendiente()
        huella = self.huella()
        bloqueo_real = self.flota._bloqueo
        @contextmanager
        def espera():
            with bloqueo_real:
                self.fecha += timedelta(hours=1)
                yield
        self.flota._bloqueo = espera()
        with self.assertRaises(SesionNoAutorizada):
            self.resolver(self.admin_token, huella)
        with conectar(self.base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_operacion").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM intenciones_descartadas").fetchone()[0], 0)
