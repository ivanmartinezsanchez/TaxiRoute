"""OAuth probado sin navegador, sin red y sin leer el almacén del usuario."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import unittest

from taxiroute.oauth_gmail import (
    ALCANCE_ENVIO, CLAVE_ALMACEN, SERVICIO_ALMACEN, ConfiguracionOAuthError,
    autorizar_gmail, cliente_gmail_autorizado,
)


class PruebasOAuthGmail(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cliente.json"
        self.ruta.write_text("{}", encoding="utf-8")
        self.almacen = Mock()
        self.almacen.get_password.return_value = None
        self.parche = patch("taxiroute.oauth_gmail._almacen_windows", return_value=self.almacen)
        self.parche.start()
        self.addCleanup(self.parche.stop)

    @patch("taxiroute.oauth_gmail.InstalledAppFlow")
    def test_configura_pkce_y_guarda_token_sin_abrir_navegador_real(self, clase):
        flujo = clase.from_client_secrets_file.return_value
        credenciales = flujo.run_local_server.return_value
        credenciales.refresh_token = "token-ficticio"
        credenciales.to_json.return_value = "credenciales-ficticias"
        autorizar_gmail(self.ruta, cuenta_sugerida="prueba@example.invalid")
        clase.from_client_secrets_file.assert_called_once_with(
            str(self.ruta), scopes=[ALCANCE_ENVIO], autogenerate_code_verifier=True)
        opciones = flujo.run_local_server.call_args.kwargs
        self.assertEqual((opciones["host"], opciones["port"]), ("127.0.0.1", 0))
        self.assertEqual(opciones["timeout_seconds"], 180)
        self.assertIsNone(opciones["authorization_prompt_message"])
        self.almacen.set_password.assert_called_once_with(
            SERVICIO_ALMACEN, CLAVE_ALMACEN, "credenciales-ficticias")

    @patch("taxiroute.oauth_gmail.InstalledAppFlow")
    def test_no_sobrescribe_credenciales_existentes(self, clase):
        self.almacen.get_password.return_value = "existentes"
        with self.assertRaises(ConfiguracionOAuthError):
            autorizar_gmail(self.ruta)
        clase.from_client_secrets_file.assert_not_called()
        self.almacen.set_password.assert_not_called()

    @patch("taxiroute.oauth_gmail.InstalledAppFlow")
    def test_sin_refresh_token_no_guarda(self, clase):
        clase.from_client_secrets_file.return_value.run_local_server.return_value.refresh_token = None
        with self.assertRaises(ConfiguracionOAuthError):
            autorizar_gmail(self.ruta)
        self.almacen.set_password.assert_not_called()

    def test_sin_cliente_o_sin_credenciales_guardadas(self):
        with self.assertRaises(ConfiguracionOAuthError):
            autorizar_gmail(self.ruta.parent / "inexistente.json")
        with self.assertRaises(ConfiguracionOAuthError):
            cliente_gmail_autorizado()

    @patch("taxiroute.oauth_gmail.build")
    @patch("taxiroute.oauth_gmail.Credentials")
    def test_carga_y_renueva_token_en_almacen_simulado(self, clase, construir):
        self.almacen.get_password.return_value = "{}"
        credenciales = clase.from_authorized_user_info.return_value
        credenciales.valid = False
        credenciales.refresh_token = "ficticio"
        credenciales.to_json.return_value = "renovado"
        cliente_gmail_autorizado()
        credenciales.refresh.assert_called_once()
        self.almacen.set_password.assert_called_once_with(
            SERVICIO_ALMACEN, CLAVE_ALMACEN, "renovado")
        construir.assert_called_once_with(
            "gmail", "v1", credentials=credenciales, cache_discovery=False)

    @patch("taxiroute.oauth_gmail.keyring.get_keyring")
    def test_backend_no_verificado_se_rechaza(self, backend):
        self.parche.stop()
        backend.return_value = Mock()
        from taxiroute.oauth_gmail import _almacen_windows
        with self.assertRaises(ConfiguracionOAuthError):
            _almacen_windows()
