"""Autorización local de Gmail: uso explícito, sin conexión al importar."""
import json
import logging
from pathlib import Path

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


ALCANCE_ENVIO = "https://www.googleapis.com/auth/gmail.send"
SERVICIO_ALMACEN = "TaxiRoute.Gmail.OAuth"
CLAVE_ALMACEN = "cuenta-remitente"


class ConfiguracionOAuthError(RuntimeError):
    """La autorización o el almacén privado no están disponibles."""


class _NoRegistrar(logging.Filter):
    def filter(self, record):
        return False


def _almacen_windows():
    """No aceptar fallbacks de texto ni backends no verificados."""
    almacen = keyring.get_keyring()
    tipo = type(almacen)
    if tipo.__module__ != "keyring.backends.Windows" or tipo.__name__ != "WinVaultKeyring":
        raise ConfiguracionOAuthError(
            "No está disponible el Administrador de credenciales de Windows."
        )
    return almacen


def autorizar_gmail(ruta_cliente, *, cuenta_sugerida=None):
    """Abre el navegador solo al llamarse expresamente y guarda tokens en Windows.

    El usuario elige y autoriza la cuenta en Google. La sugerencia de cuenta
    no prueba su identidad; se debe comprobar visualmente durante el consentimiento.
    """
    ruta = Path(ruta_cliente)
    if not ruta.is_file():
        raise ConfiguracionOAuthError("No se encuentra el archivo del cliente OAuth.")
    almacen = _almacen_windows()
    if almacen.get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN) is not None:
        raise ConfiguracionOAuthError("Ya existe una autorización guardada; revísala antes de sustituirla.")
    flujo = InstalledAppFlow.from_client_secrets_file(
        str(ruta), scopes=[ALCANCE_ENVIO], autogenerate_code_verifier=True,
    )
    opciones = {
        "host": "127.0.0.1",
        "port": 0,
        "timeout_seconds": 180,
        "authorization_prompt_message": None,
        "success_message": "Autorización completada. Puedes cerrar esta ventana y volver a TaxiRoute.",
        "access_type": "offline",
    }
    if cuenta_sugerida:
        opciones["login_hint"] = cuenta_sugerida
    # El servidor de retorno no debe registrar la URL con el código OAuth.
    logger = logging.getLogger("google_auth_oauthlib.flow")
    filtro = _NoRegistrar()
    logger.addFilter(filtro)
    try:
        credenciales = flujo.run_local_server(**opciones)
    finally:
        logger.removeFilter(filtro)
    if not credenciales.refresh_token:
        raise ConfiguracionOAuthError("Google no devolvió un token de renovación; no se ha guardado.")
    almacen.set_password(SERVICIO_ALMACEN, CLAVE_ALMACEN, credenciales.to_json())


def cliente_gmail_autorizado():
    """Recupera las credenciales existentes y construye el cliente Gmail.

    No abre el navegador. Los errores se propagan sin imprimir credenciales.
    La actualización se conserva en el almacén del sistema.
    """
    almacen = _almacen_windows()
    contenido = almacen.get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN)
    if contenido is None:
        raise ConfiguracionOAuthError("Gmail aún no está autorizado.")
    credenciales = Credentials.from_authorized_user_info(
        json.loads(contenido), scopes=[ALCANCE_ENVIO],
    )
    if not credenciales.valid:
        if not credenciales.refresh_token:
            raise ConfiguracionOAuthError("La autorización no puede renovarse.")
        credenciales.refresh(Request())
        almacen.set_password(SERVICIO_ALMACEN, CLAVE_ALMACEN, credenciales.to_json())
    return build("gmail", "v1", credentials=credenciales, cache_discovery=False)
