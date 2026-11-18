"""Comprueba parcialmente el código local frente a un contenedor ya verificado.

Solo sirve para advertir de diferencias: no acredita procedencia ni decide si
una imagen es segura. No lee datos de usuario ni consulta el almacén de Gmail.
"""
import hashlib
import json
from pathlib import Path
import re

from taxiroute.cargar_gmail_windows import _docker


ARCHIVOS = ("servidor_proxy_experimental.py", "servidor_sqlite_local.py",
            "api_durable_experimental.py", "web_integrada.py")
_PROGRAMA = r'''import hashlib,json,pathlib
base=pathlib.Path("/app/taxiroute")
nombres=("servidor_proxy_experimental.py","servidor_sqlite_local.py","api_durable_experimental.py","web_integrada.py")
print(json.dumps({nombre:hashlib.sha256((base/nombre).read_bytes()).hexdigest() for nombre in nombres},sort_keys=True))'''


def codigo_distinto(identificador, *, carpeta=None):
    """True si difiere algún archivo, False si coinciden, None si no se pudo comprobar."""
    if not isinstance(identificador, str) or re.fullmatch(r"[0-9a-f]{64}", identificador) is None:
        return None
    carpeta = Path(__file__).resolve().parent if carpeta is None else Path(carpeta)
    try:
        local = {nombre: hashlib.sha256((carpeta / nombre).read_bytes()).hexdigest() for nombre in ARCHIVOS}
        salida = _docker(["exec", identificador, "python", "-c", _PROGRAMA])
        remoto = json.loads(salida.decode("utf-8"))
        if not isinstance(remoto, dict) or set(remoto) != set(ARCHIVOS):
            return None
        if any(not isinstance(valor, str) or re.fullmatch(r"[0-9a-f]{64}", valor) is None for valor in remoto.values()):
            return None
        return local != remoto
    except (OSError, ValueError, UnicodeError, TypeError, RuntimeError):
        return None
