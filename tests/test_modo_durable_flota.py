"""Barrera experimental: solo pruebas con datos y bases temporales."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria, OperacionDurableRequerida
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones


class PruebasBarreraDurable(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "base.sqlite3"
        inicializar_base(self.ruta)
        usuario = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana",
                               contrasena="ConductorPrueba123!")
        self.usuario = usuario.id
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "ConductorPrueba123!").token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(), modo_durable_experimental=True)

    def test_todas_las_entradas_anteriores_quedan_bloqueadas(self):
        operaciones = (
            lambda: self.flota.ejecutar(self.usuario, "iniciar"),
            lambda: self.flota.ejecutar_si_revision(self.usuario, "iniciar", 0),
            lambda: self.flota.ejecutar_controlado(self.usuario, "iniciar", 0),
            lambda: self.flota.guardar_captura(self.ruta, self.usuario),
            lambda: self.flota.preparar_cierre_experimental(self.sesiones, self.token, 0),
        )
        for operacion in operaciones:
            with self.subTest(operacion=operacion), self.assertRaises(OperacionDurableRequerida):
                operacion()
        self.assertIsNone(self.flota.consultar(self.usuario))
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT count(*) FROM carreras_recuperacion").fetchone()[0], 0)

    def test_via_experimental_disponible_y_metodos_antiguos_siguen_bloqueados(self):
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador,
                            accion="iniciar", revision=0)
        punto = self.flota.aplicar_intencion_experimental(self.sesiones, self.token, identificador)
        self.assertEqual(punto.usuario_id, self.usuario)
        self.assertEqual(self.flota.consultar(self.usuario).revision, 1)
        with self.assertRaises(OperacionDurableRequerida):
            self.flota.ejecutar(self.usuario, "mover")
        self.assertEqual(self.flota.capturar(self.usuario).carrera.estado.value, "parado")

    def test_modo_ordinario_y_validacion_de_opcion(self):
        normal = FlotaEnMemoria(lambda _: ServicioCarreras())
        self.assertTrue(normal.ejecutar(self.usuario, "iniciar").activa)
        for valor in (1, "si", None):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                FlotaEnMemoria(lambda _: ServicioCarreras(), modo_durable_experimental=valor)
