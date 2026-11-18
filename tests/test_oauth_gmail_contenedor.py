"""Pruebas con secretos ficticios; no acceden a Google ni a las cuentas reales."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.oauth_gmail import ALCANCE_ENVIO, ConfiguracionOAuthError
from taxiroute.oauth_gmail_contenedor import cliente_gmail_desde_secreto


class PruebasOAuthContenedor(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "secreto.json"
        self.datos = {"client_id": "id-ficticio", "client_secret": "secreto-ficticio",
                      "refresh_token": "token-ficticio", "token_uri": "https://oauth2.googleapis.com/token",
                      "scopes": [ALCANCE_ENVIO]}
        self.ruta.write_text(json.dumps(self.datos), encoding="utf-8")

    @patch("taxiroute.oauth_gmail_contenedor.build")
    @patch("taxiroute.oauth_gmail_contenedor.Request")
    @patch("taxiroute.oauth_gmail_contenedor.Credentials")
    def test_renueva_en_memoria_y_no_modifica_archivo(self, credenciales, request, build):
        contenido = self.ruta.read_bytes()
        cliente_gmail_desde_secreto(self.ruta)
        self.assertEqual(self.ruta.read_bytes(), contenido)
        self.assertEqual(credenciales.call_args.kwargs["scopes"], [ALCANCE_ENVIO])
        credenciales.return_value.refresh.assert_called_once_with(request.return_value)
        build.assert_called_once_with("gmail", "v1", credentials=credenciales.return_value, cache_discovery=False)

    @patch("taxiroute.oauth_gmail_contenedor.Credentials")
    def test_falla_cerrado_sin_exponer_datos(self, credenciales):
        casos = [None, {}, self.datos | {"token_uri": "https://ejemplo.invalid/token"},
                 self.datos | {"refresh_token": ""}, self.datos | {"scopes": []}]
        for caso in casos:
            with self.subTest(caso=caso):
                self.ruta.write_text(json.dumps(caso), encoding="utf-8")
                with self.assertRaises(ConfiguracionOAuthError) as fallo:
                    cliente_gmail_desde_secreto(self.ruta)
                self.assertNotIn("token-ficticio", str(fallo.exception))
        credenciales.assert_not_called()

    @patch("taxiroute.oauth_gmail_contenedor.Credentials")
    def test_falta_archivo_o_tamano_excesivo(self, credenciales):
        for ruta in (self.ruta.parent / "ausente", self.ruta):
            if ruta == self.ruta:
                ruta.write_text("x" * 32769, encoding="utf-8")
            with self.assertRaises(ConfiguracionOAuthError):
                cliente_gmail_desde_secreto(ruta)
        credenciales.assert_not_called()

    @patch("taxiroute.oauth_gmail_contenedor.build")
    @patch("google.oauth2.credentials.Credentials.refresh")
    def test_acepta_formato_serializado_por_google_con_datos_ficticios(self, renovar, build):
        from google.oauth2.credentials import Credentials
        original = Credentials(token=None, refresh_token="token-ficticio",
                               token_uri="https://oauth2.googleapis.com/token", client_id="id-ficticio",
                               client_secret="secreto-ficticio", scopes=[ALCANCE_ENVIO])
        self.ruta.write_text(original.to_json(), encoding="utf-8")
        cliente_gmail_desde_secreto(self.ruta)
        renovar.assert_called_once()
        self.assertIsInstance(build.call_args.kwargs["credentials"], Credentials)

    @patch("taxiroute.oauth_gmail_contenedor.build")
    @patch("taxiroute.oauth_gmail_contenedor.Credentials")
    def test_error_renovacion_no_muestra_detalles(self, credenciales, build):
        credenciales.return_value.refresh.side_effect = RuntimeError("secreto-ficticio")
        with self.assertRaises(ConfiguracionOAuthError) as fallo:
            cliente_gmail_desde_secreto(self.ruta)
        self.assertNotIn("secreto-ficticio", str(fallo.exception))
        self.assertIsNone(fallo.exception.__cause__)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
