"""Recorrido de administración con Gmail simulado y archivo OAuth ficticio."""
import base64
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import re
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.correo_gmail import EnviadorGmail
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.oauth_gmail_contenedor import cliente_gmail_desde_secreto


class PruebasCorreoWebContenedor(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.datos = preparar_demo_sqlite(carpeta / "demo")
        secreto_externo = os.environ.get("TAXIROUTE_SECRETO_PRUEBA")
        self.secreto = Path(secreto_externo) if secreto_externo else carpeta / "oauth.json"
        if not secreto_externo:
            self.secreto.write_text(json.dumps({"client_id": "id-ficticio", "client_secret": "secreto-ficticio",
                "refresh_token": "token-ficticio", "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [ALCANCE_ENVIO]}), encoding="utf-8")
        self.gmail = Mock()
        self.parche_refresco = patch("google.oauth2.credentials.Credentials.refresh")
        self.parche_api = patch("taxiroute.oauth_gmail_contenedor.build", return_value=self.gmail)
        self.parche_refresco.start()
        self.parche_api.start()
        self.addCleanup(self.parche_refresco.stop)
        self.addCleanup(self.parche_api.stop)
        def enviar(destino, codigo):
            EnviadorGmail(cliente_gmail_desde_secreto(self.secreto))(destino, codigo)
        contexto = abrir_app_experimental(ruta_cuentas=self.datos / "cuentas.sqlite3",
            ruta_historial=self.datos / "historial.sqlite3", ruta_tarifas=self.datos / "tarifas.json",
            clave_hmac=token_bytes(32), enviar_pin=enviar)
        self.app = contexto.__enter__()
        self.addCleanup(contexto.__exit__, None, None, None)
        self.cliente = self.app.test_client()

    def post(self, ruta, datos):
        return self.cliente.post(ruta, base_url="https://localhost",
            headers={"Origin": "https://localhost"}, json=datos)

    def test_pin_simulado_permita_acceso_y_no_se_reutiliza(self):
        inicio = self.post("/acceso/administrador", {"usuario": "AdminDemo", "contrasena": "AdminDemo456!"})
        self.assertEqual(inicio.status_code, 200)
        llamada = self.gmail.users.return_value.messages.return_value.send
        mensaje = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(llamada.call_args.kwargs["body"]["raw"]))
        self.assertEqual(mensaje["To"], "demo@example.invalid")
        codigo = re.search(r"Tu código es: ([0-9]{7})", mensaje.get_content()).group(1)
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 401)
        self.assertEqual(self.post("/acceso/administrador/pin", {"codigo": codigo}).status_code, 200)
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 200)
        self.assertEqual(self.post("/acceso/administrador/pin", {"codigo": codigo}).status_code, 401)

    def test_fallo_gmail_no_autoriza_ni_expone_error(self):
        self.gmail.users.return_value.messages.return_value.send.return_value.execute.side_effect = RuntimeError("detalle-ficticio")
        respuesta = self.post("/acceso/administrador", {"usuario": "AdminDemo", "contrasena": "AdminDemo456!"})
        self.assertEqual(respuesta.status_code, 401)
        self.assertNotIn("detalle-ficticio", respuesta.get_data(as_text=True))
        self.assertEqual(self.cliente.get("/administracion", base_url="https://localhost").status_code, 401)


if __name__ == "__main__":
    unittest.main()
