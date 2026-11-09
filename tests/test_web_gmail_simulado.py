"""Recorrido web con el adaptador Gmail simulado: nunca usa red ni credenciales."""
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.correo_gmail import EnviadorGmail
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.inicializacion_administrador import preparar_primer_administrador
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


class PruebasWebConGmailSimulado(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.ruta = carpeta / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "PruebaAdmin123!"
        cuenta = crear_cuenta(self.ruta, nombre_usuario="AdminPrueba", nombre_visible="Admin de prueba",
                              contrasena=self.clave, rol="administrador")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                             (cuenta.id, "prueba@example.invalid"))
        fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.gmail = Mock()
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), enviar=EnviadorGmail(self.gmail),
                          reloj=lambda: fecha)
        sesiones = ServicioSesiones(self.ruta, reloj=lambda: fecha)
        def historial(usuario):
            return HistorialCSV(carpeta / usuario / "historial.csv")
        flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(historial=historial(usuario)))
        app = crear_app_integrada(sesiones, pin, flota, historial)
        app.testing = True
        self.cliente = app.test_client()

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url="https://localhost",
                                 headers={"Origin": "https://localhost"}, json=datos)

    def test_pin_por_adaptador_y_acceso_sin_exponer_codigo(self):
        inicio = self.post("/acceso/administrador", {"usuario": "AdminPrueba", "contrasena": self.clave})
        self.assertEqual(inicio.status_code, 200)
        self.assertEqual(inicio.json["paso"], "pin_pendiente")
        envio = self.gmail.users.return_value.messages.return_value.send
        envio.assert_called_once()
        envio.return_value.execute.assert_called_once_with()
        mensaje = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(
            envio.call_args.kwargs["body"]["raw"]))
        self.assertEqual(mensaje["To"], "prueba@example.invalid")
        codigo = re.search(r"Tu código es: ([0-9]{7})", mensaje.get_content()).group(1)
        self.assertNotIn(codigo, inicio.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 401)
        verificacion = self.post("/acceso/administrador/pin", {"codigo": codigo})
        self.assertEqual(verificacion.json["paso"], "acceso_completado")
        self.assertNotIn(codigo, verificacion.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 200)
        self.assertEqual(self.post("/acceso/administrador/pin", {"codigo": codigo}).status_code, 401)

    def test_contrasena_incorrecta_no_llama_a_gmail(self):
        respuesta = self.post("/acceso/administrador", {"usuario": "AdminPrueba", "contrasena": "incorrecta"})
        self.assertEqual(respuesta.status_code, 401)
        self.gmail.users.assert_not_called()
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 401)

    def test_fallo_del_proveedor_no_da_acceso_ni_expone_detalles(self):
        self.gmail.users.return_value.messages.return_value.send.return_value.execute.side_effect = (
            RuntimeError("detalle privado del proveedor"))
        respuesta = self.post("/acceso/administrador", {"usuario": "AdminPrueba", "contrasena": self.clave})
        self.assertEqual(respuesta.status_code, 401)
        self.assertNotIn("detalle privado", respuesta.get_data(as_text=True))
        self.assertNotIn("taxiroute_pin_pendiente=", " ".join(respuesta.headers.getlist("Set-Cookie")))
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 401)
        with conectar(self.ruta) as conexion:
            desafio = conexion.execute("SELECT estado_envio, invalidado_en FROM desafios_acceso").fetchone()
        self.assertEqual(desafio["estado_envio"], "fallido")
        self.assertIsNotNone(desafio["invalidado_en"])

    def test_primer_administrador_cambia_clave_y_accede_con_correo_simulado(self):
        with TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ruta = carpeta / "primera.sqlite3"
            inicializar_base(ruta)
            preparar_primer_administrador(ruta, nombre_usuario="PrimerAdmin", nombre_visible="Prueba",
                contrasena="TemporalPrueba123!", correo="prueba@example.invalid")
            ahora = [datetime(2026, 11, 9, tzinfo=timezone.utc)]
            cliente_gmail = Mock()
            pin = ServicioPIN(ruta, clave_hmac=token_bytes(32), enviar=EnviadorGmail(cliente_gmail),
                              reloj=lambda: ahora[0])
            sesiones = ServicioSesiones(ruta, reloj=lambda: ahora[0])
            def historial(usuario):
                return HistorialCSV(carpeta / usuario / "historial.csv")
            flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(historial=historial(usuario)))
            app = crear_app_integrada(sesiones, pin, flota, historial)
            app.testing = True
            cliente = app.test_client()
            def post(ruta_web, datos):
                return cliente.post(ruta_web, base_url="https://localhost",
                                    headers={"Origin": "https://localhost"}, json=datos)
            def ultimo_codigo():
                llamada = cliente_gmail.users.return_value.messages.return_value.send
                mensaje = BytesParser(policy=policy.default).parsebytes(
                    base64.urlsafe_b64decode(llamada.call_args.kwargs["body"]["raw"]))
                self.assertEqual(mensaje["To"], "prueba@example.invalid")
                return re.search(r"Tu código es: ([0-9]{7})", mensaje.get_content()).group(1)

            inicio = post("/acceso/administrador", {"usuario": "PrimerAdmin", "contrasena": "TemporalPrueba123!"})
            self.assertEqual(inicio.json["paso"], "pin_pendiente")
            comprobacion = post("/acceso/administrador/pin", {"codigo": ultimo_codigo()})
            self.assertEqual(comprobacion.json["paso"], "cambio_pendiente")
            self.assertEqual(cliente.get("/administracion", base_url="https://localhost").status_code, 401)
            cambio = post("/acceso/administrador/cambio", {"contrasena_nueva": "NuevaClave456!"})
            self.assertEqual(cambio.json["paso"], "volver_a_iniciar")
            self.assertEqual(cliente.get("/administracion", base_url="https://localhost").status_code, 401)
            ahora[0] += timedelta(minutes=2)
            inicio = post("/acceso/administrador", {"usuario": "PrimerAdmin", "contrasena": "NuevaClave456!"})
            self.assertEqual(inicio.json["paso"], "pin_pendiente")
            self.assertEqual(post("/acceso/administrador/pin", {"codigo": ultimo_codigo()}).json["paso"],
                             "acceso_completado")
            self.assertEqual(cliente.get("/administracion", base_url="https://localhost").status_code, 200)
            self.assertEqual(cliente_gmail.users.return_value.messages.return_value.send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
