"""Pruebas de consulta web con datos y certificados simulados."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.pin import ServicioPIN
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_acceso import NOMBRE_COOKIE
from taxiroute.web_integrada import crear_app_integrada


class PruebasWebPendientes(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        clave = "FicticiaLarga123!"
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=clave)
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena=clave)
        admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin", contrasena=clave, rol="administrador")
        with conectar(self.ruta) as c:
            c.execute("UPDATE usuarios SET cambio_obligatorio=0")
            c.execute("INSERT INTO configuracion_administrador VALUES (?, ?)", (admin.id, "demo@example.invalid"))
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), reloj=lambda: self.fecha,
                          enviar=lambda correo, codigo: buzon.append(codigo))
        desafio = pin.solicitar("Admin", clave)
        tokens = [self.sesiones.iniciar_conductor(nombre, clave).token for nombre in ("Ana", "Luis")]
        tokens.append(pin.verificar(desafio, buzon[-1]).sesion.token)
        self.tokens = tokens
        historial = lambda usuario: HistorialCSV(carpeta / usuario / "historial.csv")
        flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(historial=historial(usuario)))
        app = crear_app_integrada(self.sesiones, pin, flota, historial)
        app.testing = True
        self.clientes = [app.test_client() for _ in tokens]
        for cliente, token in zip(self.clientes, tokens):
            cliente.set_cookie(NOMBRE_COOKIE, token, secure=True)
        self.anonimo = app.test_client()
        self.punto = PuntoControl("carrera-ana", self.ana.id, self.fecha,
                                  self.fecha + timedelta(seconds=5), "parado",
                                  5_000_000_000, 0, "0.02", "0.05", 1)
        self.ruta_web = "/api/recuperacion/pendiente"

    def get(self, cliente, ruta=None, base="https://localhost"):
        return cliente.get(ruta or self.ruta_web, base_url=base)

    def test_sesion_y_separacion_de_cuentas_incluido_admin(self):
        guardar_punto(self.ruta, self.punto)
        self.assertEqual(self.get(self.anonimo).status_code, 401)
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["pendiente"]["carrera_id"], "carrera-ana")
        self.assertEqual(respuesta.json["pendiente"]["parado_ns"], "5000000000")
        self.assertIsNone(respuesta.json["pendiente"]["importe_final"])
        self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
        for cliente in self.clientes[1:]:
            self.assertIsNone(self.get(cliente).json["pendiente"])
        self.assertEqual(self.get(self.clientes[1], self.ruta_web + "?usuario=Ana").status_code, 400)

    def test_caducidad_y_no_renovacion(self):
        guardar_punto(self.ruta, self.punto)
        self.fecha += timedelta(minutes=59)
        self.assertEqual(self.get(self.clientes[0]).status_code, 200)
        self.fecha += timedelta(minutes=1)
        self.assertEqual(self.get(self.clientes[0]).status_code, 401)

    def test_datos_corruptos_no_exponen_detalles(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as c:
            c.execute("UPDATE carreras_recuperacion SET ultimo_control='fecha-invalida'")
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("fecha-invalida", respuesta.get_data(as_text=True))
        self.assertIsNone(self.get(self.clientes[1]).json["pendiente"])

    def test_http_y_operaciones_de_escritura_se_rechazan(self):
        self.assertEqual(self.get(self.clientes[0], base="http://localhost").status_code, 400)
        self.assertEqual(self.clientes[0].post(self.ruta_web, base_url="https://localhost").status_code, 405)

    def test_intencion_de_inicio_solo_es_visible_para_su_cuenta_y_no_cambia_datos(self):
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=identificador,
                            accion="iniciar", revision=0)
        with conectar(self.ruta) as conexion:
            anterior = tuple(conexion.execute("SELECT * FROM intenciones_operacion").fetchone())
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.json["pendiente"])
        self.assertEqual(respuesta.json["intencion"], {"accion": "iniciar"})
        self.assertNotIn(identificador, respuesta.get_data(as_text=True))
        self.assertNotIn(self.ana.id, respuesta.get_data(as_text=True))
        for cliente in self.clientes[1:]:
            self.assertIsNone(self.get(cliente).json["intencion"])
        with conectar(self.ruta) as conexion:
            self.assertEqual(tuple(conexion.execute("SELECT * FROM intenciones_operacion").fetchone()), anterior)

    def test_intencion_de_movimiento_muestra_accion_y_punto_guardado(self):
        guardar_punto(self.ruta, self.punto)
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=str(uuid4()),
                            accion="mover", revision=1, carrera_id=self.punto.carrera_id)
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["intencion"], {"accion": "mover"})
        self.assertEqual(respuesta.json["pendiente"]["estado_vehiculo"], "parado")

    def test_intencion_corrupta_o_sesion_revocada_no_da_diagnostico(self):
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=str(uuid4()),
                            accion="iniciar", revision=0)
        with conectar(self.ruta) as conexion:
            conexion.execute("PRAGMA ignore_check_constraints=ON")
            conexion.execute("UPDATE intenciones_operacion SET accion='no-valida'")
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("no-valida", respuesta.get_data(as_text=True))
        self.assertIsNone(self.get(self.clientes[1]).json["intencion"])
        self.sesiones.cerrar(self.tokens[0])
        self.assertEqual(self.get(self.clientes[0]).status_code, 401)

    def test_intencion_de_parar_y_datos_incompatibles(self):
        punto = replace(self.punto, estado_vehiculo="en movimiento")
        guardar_punto(self.ruta, punto)
        registrar_intencion(self.sesiones, self.tokens[0], id_intencion=str(uuid4()),
                            accion="parar", revision=1, carrera_id=punto.carrera_id)
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["intencion"], {"accion": "parar"})
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE intenciones_operacion SET carrera_id='carrera-ajena'")
        respuesta = self.get(self.clientes[0])
        self.assertEqual(respuesta.status_code, 503)
        self.assertNotIn("carrera-ajena", respuesta.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
