"""Envío de correos simulado, sin acceso a Google ni red."""
import base64
from email import policy
from email.parser import BytesParser
import unittest
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from taxiroute.correo_gmail import EnviadorGmail, ErrorEnvioPIN
from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN


class PruebasCorreoGmail(unittest.TestCase):
    def setUp(self):
        self.cliente = Mock()
        self.enviar = EnviadorGmail(self.cliente)

    def test_mensaje_y_api_sin_enviar_correo_real(self):
        self.enviar("prueba@example.invalid", "1234567")
        llamada = self.cliente.users.return_value.messages.return_value.send
        llamada.assert_called_once()
        self.assertEqual(llamada.call_args.kwargs["userId"], "me")
        contenido = llamada.call_args.kwargs["body"]["raw"]
        mensaje = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(contenido))
        self.assertEqual(mensaje["To"], "prueba@example.invalid")
        self.assertIn("1234567", mensaje.get_content())
        self.assertIn("cinco minutos", mensaje.get_content())
        llamada.return_value.execute.assert_called_once_with()

    def test_rechaza_destinos_o_codigos_invalidos_antes_de_enviar(self):
        for destino, codigo in (
            ("prueba@example.invalid\\nBcc:otro@example.invalid", "1234567"),
            ("uno@example.invalid,otro@example.invalid", "1234567"),
            ("@example.invalid", "1234567"),
            ("usuario@", "1234567"),
            ("prueba@example.invalid", "12abc67"),
            ("prueba@example.invalid", "123456"),
        ):
            with self.subTest(destino=destino), self.assertRaises(ValueError):
                self.enviar(destino, codigo)
        self.cliente.users.assert_not_called()

    def test_fallo_proveedor_no_expone_detalles(self):
        self.cliente.users.return_value.messages.return_value.send.return_value.execute.side_effect = (
            RuntimeError("detalle privado y codigo 1234567")
        )
        with self.assertRaises(ErrorEnvioPIN) as error:
            self.enviar("prueba@example.invalid", "1234567")
        self.assertNotIn("1234567", str(error.exception))
        self.assertIsNone(error.exception.__cause__)


class PruebasIntegracionGmail(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "AdminFicticio123!"
        cuenta = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Prueba",
                             contrasena=self.clave, rol="administrador")
        with conectar(self.ruta) as c:
            c.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                      (cuenta.id, "prueba@example.invalid"))
        self.cliente = Mock()
        self.pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
            enviar=EnviadorGmail(self.cliente),
            reloj=lambda: datetime(2026, 11, 8, 12, tzinfo=timezone.utc))

    def test_envio_simulado_permite_verificar_el_codigo(self):
        desafio = self.pin.solicitar("Admin", self.clave)
        llamada = self.cliente.users.return_value.messages.return_value.send
        mensaje = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(llamada.call_args.kwargs["body"]["raw"]))
        import re
        codigo = re.search(r"Tu código es: ([0-9]{7})", mensaje.get_content()).group(1)
        self.assertIsNotNone(self.pin.verificar(desafio, codigo))

    def test_fallo_simulado_invalida_desafio(self):
        self.cliente.users.return_value.messages.return_value.send.return_value.execute.side_effect = OSError("fallo")
        self.assertIsNone(self.pin.solicitar("Admin", self.clave))
        with conectar(self.ruta) as c:
            fila = c.execute("SELECT * FROM desafios_acceso").fetchone()
        self.assertEqual(fila["estado_envio"], "fallido")
        self.assertIsNotNone(fila["invalidado_en"])
