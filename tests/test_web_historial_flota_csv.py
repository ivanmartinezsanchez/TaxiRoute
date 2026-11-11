"""Consulta de flota CSV con cuentas y datos exclusivamente temporales."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import ResumenCarrera
from taxiroute.cuentas import crear_cuenta
from taxiroute.historial import HistorialCSV
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_historial_flota_csv import crear_historial_flota_csv_web
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.preparar_csv_local import preparar_archivos_csv


class PruebasHistorialFlotaCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.ruta = self.carpeta / "cuentas.sqlite3"
        self.historiales = self.carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.ruta)
        clave = "FicticiaLarga123!"
        self.admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Administración", contrasena=clave, rol="administrador")
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (self.admin.id, "demo@example.invalid"))
        self.fecha = datetime.now(timezone.utc)
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), enviar=lambda correo, codigo: buzon.append(codigo), reloj=lambda: self.fecha)
        desafio = pin.solicitar("Admin", clave)
        token_admin = pin.verificar(desafio, buzon[-1]).sesion.token
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        token_ana = self.sesiones.iniciar_conductor("Ana", clave).token
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(crear_historial_flota_csv_web(self.sesiones, self.historiales))
        self.web, self.conductor, self.anonimo = app.test_client(), app.test_client(), app.test_client()
        self.web.set_cookie(NOMBRE_COOKIE, token_admin, secure=True)
        self.conductor.set_cookie(NOMBRE_COOKIE, token_ana, secure=True)
        self.url = "https://localhost"
        self.endpoint = "/api/administracion/historial"

    def guardar(self, cuenta, instante):
        resumen = ResumenCarrera(identificador=str(uuid4()), inicio=instante-timedelta(seconds=10), fin=instante,
            segundos_parado=Decimal("10"), segundos_movimiento=Decimal("0"), tarifa_parado=Decimal("0.02"),
            tarifa_movimiento=Decimal("0.05"), importe=Decimal("0.20"))
        HistorialCSV(self.historiales / cuenta.id / "historial.csv").guardar(resumen)

    def test_autorizacion_fechas_y_aislamiento(self):
        self.guardar(self.ana, self.fecha)
        self.guardar(self.admin, self.fecha-timedelta(days=3))
        legado = self.historiales / "historial.csv"
        legado.write_text("datos antiguos", encoding="utf-8")
        self.assertEqual(self.anonimo.get(self.endpoint, base_url=self.url).status_code, 401)
        self.assertEqual(self.conductor.get(self.endpoint, base_url=self.url).status_code, 403)
        hoy = self.web.get(self.endpoint, base_url=self.url)
        self.assertEqual(hoy.status_code, 200)
        self.assertEqual([f["conductor"] for f in hoy.json["carreras"]], ["Ana"])
        todas = self.web.get(self.endpoint + "?periodo=todas", base_url=self.url).json
        self.assertEqual({f["conductor"] for f in todas["carreras"]}, {"Ana", "Administración"})
        self.assertEqual(legado.read_text(encoding="utf-8"), "datos antiguos")
        self.assertFalse((self.historiales / self.admin.id / "historial.csv").is_symlink())
        self.assertEqual(self.web.get(self.endpoint + "?usuario=otro", base_url=self.url).status_code, 400)

    def test_paginas(self):
        for _ in range(101):
            self.guardar(self.ana, self.fecha)
        primera = self.web.get(self.endpoint + "?periodo=todas", base_url=self.url).json
        segunda = self.web.get(self.endpoint + "?periodo=todas&pagina=2", base_url=self.url).json
        self.assertEqual((len(primera["carreras"]), primera["hay_mas"]), (100, True))
        self.assertEqual((len(segunda["carreras"]), segunda["hay_mas"]), (1, False))

    def test_revalidacion_de_sesion(self):
        original = self.sesiones.ejecutar_con_sesion_y_conexion
        llamadas = 0
        def revocar(*args, **kwargs):
            nonlocal llamadas
            llamadas += 1
            if llamadas == 2:
                self.sesiones.cerrar(self.web.get_cookie(NOMBRE_COOKIE).value)
            return original(*args, **kwargs)
        with patch.object(self.sesiones, "ejecutar_con_sesion_y_conexion", side_effect=revocar):
            self.assertEqual(self.web.get(self.endpoint, base_url=self.url).status_code, 401)

    def test_csv_incompleto_no_se_muestra(self):
        ruta = self.historiales / self.ana.id / "historial.csv"
        ruta.parent.mkdir()
        ruta.write_text("corrupto", encoding="utf-8")
        self.assertEqual(self.web.get(self.endpoint, base_url=self.url).status_code, 503)

    def test_historial_ocupado_no_entrega_lectura_parcial(self):
        self.guardar(self.ana, self.fecha)
        ruta = self.historiales / self.ana.id / "historial.csv"
        original = ruta.read_bytes()
        tomado, liberar = Event(), Event()
        def ocupar():
            with bloqueo_historial(ruta):
                tomado.set()
                liberar.wait(10)
        hilo = Thread(target=ocupar)
        hilo.start()
        try:
            self.assertTrue(tomado.wait(5))
            self.assertEqual(self.web.get(self.endpoint, base_url=self.url).status_code, 503)
            self.assertEqual(ruta.read_bytes(), original)
        finally:
            liberar.set()
            hilo.join(timeout=5)
        self.assertFalse(hilo.is_alive())


class PruebasCierresDurablesCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.archivos = preparar_archivos_csv(Path(temporal.name) / "instalacion")
        clave = "FicticiaLarga123!"
        self.admin = crear_cuenta(self.archivos.cuentas, nombre_usuario="Admin", nombre_visible="Administración", contrasena=clave, rol="administrador")
        self.ana = crear_cuenta(self.archivos.cuentas, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (self.admin.id, "demo@example.invalid"))
        self.buzon = []
        self.argumentos = dict(ruta_cuentas=self.archivos.cuentas, carpeta_historial=self.archivos.historiales,
            ruta_tarifas=self.archivos.tarifas, clave_hmac=token_bytes(32),
            enviar_pin=lambda correo, codigo: self.buzon.append(codigo), durable=True)
        self.url = "https://localhost"
        self.headers = {"Origin": self.url}
        self.clave = clave

    def clientes(self, app):
        admin, conductor = app.test_client(), app.test_client()
        def post(cliente, ruta, datos):
            return cliente.post(ruta, base_url=self.url, headers=self.headers, json=datos)
        self.assertEqual(post(admin, "/acceso/administrador", {"usuario":"Admin", "contrasena":self.clave}).status_code, 200)
        self.assertEqual(post(admin, "/acceso/administrador/pin", {"codigo":self.buzon[-1]}).status_code, 200)
        self.assertEqual(post(conductor, "/acceso/conductor", {"usuario":"Ana", "contrasena":self.clave}).status_code, 200)
        return admin, conductor, post

    def test_confirmado_ausente_o_alterado(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            admin, conductor, post = self.clientes(app)
            self.assertEqual(post(conductor, "/api/carrera/iniciar", {"revision":0, "id_operacion":str(uuid4())}).status_code, 200)
            self.assertEqual(post(conductor, "/api/carrera/finalizar", {"revision":1}).status_code, 200)
            url = "/api/administracion/historial?periodo=todas"
            self.assertEqual(len(admin.get(url, base_url=self.url).json["carreras"]), 1)
            ruta = self.archivos.historiales / self.ana.id / "historial.csv"
            original = ruta.read_bytes()
            ruta.write_bytes(original.replace(b",0.02,", b",0.99,", 1))
            self.assertEqual(admin.get(url, base_url=self.url).status_code, 503)
            ruta.unlink()
            self.assertEqual(admin.get(url, base_url=self.url).status_code, 503)
            self.assertFalse(ruta.exists())

    def test_cierre_pendiente_no_se_presenta_como_guardado(self):
        with abrir_app_csv_experimental(**self.argumentos) as app:
            admin, conductor, post = self.clientes(app)
            self.assertEqual(post(conductor, "/api/carrera/iniciar", {"revision":0, "id_operacion":str(uuid4())}).status_code, 200)
            with patch("taxiroute.api_durable_experimental.conciliar_pendiente_csv_autorizado", side_effect=OSError("fallo ficticio")):
                self.assertNotEqual(post(conductor, "/api/carrera/finalizar", {"revision":1}).status_code, 200)
            respuesta = admin.get("/api/administracion/historial?periodo=todas", base_url=self.url)
            self.assertEqual(respuesta.status_code, 503)
            self.assertNotIn("carreras", respuesta.json)
