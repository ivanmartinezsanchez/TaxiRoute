"""Cliente Gmail para un secreto proporcionado al contenedor; uso experimental.

No crea ni exporta secretos y no se conecta a Gmail hasta que se solicita un PIN.
"""
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from taxiroute.oauth_gmail import ALCANCE_ENVIO, ConfiguracionOAuthError

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_MAX_BYTES = 32768


def cliente_gmail_desde_secreto(ruta):
    """Lee el secreto externo y renueva en memoria sin modificar el archivo.

    El despliegue debe proteger el archivo y limitar su acceso. Esta función no
    garantiza permisos del sistema, identidad del remitente ni aislamiento.
    """
    try:
        archivo = Path(ruta)
        if archivo.is_symlink() or not archivo.is_file():
            raise ValueError
        with archivo.open("rb") as flujo:
            contenido = flujo.read(_MAX_BYTES + 1)
        if len(contenido) > _MAX_BYTES:
            raise ValueError
        datos = json.loads(contenido)
        if not isinstance(datos, dict) or datos.get("token_uri") != _TOKEN_URI:
            raise ValueError
        if not all(isinstance(datos.get(campo), str) and datos[campo]
                   for campo in ("client_id", "client_secret", "refresh_token")):
            raise ValueError
        alcances = datos.get("scopes")
        if not isinstance(alcances, list) or ALCANCE_ENVIO not in alcances:
            raise ValueError
        credenciales = Credentials(token=None, refresh_token=datos["refresh_token"],
                                   token_uri=_TOKEN_URI, client_id=datos["client_id"],
                                   client_secret=datos["client_secret"], scopes=[ALCANCE_ENVIO])
        credenciales.refresh(Request())
        return build("gmail", "v1", credentials=credenciales, cache_discovery=False)
    except Exception:
        # No incluir rutas, tokens, respuestas del proveedor ni contenido JSON.
        raise ConfiguracionOAuthError("No se pudo utilizar la autorización de Gmail.") from None
