"""Carga una autorización en una ruta temporal; no imprime ni envía sus datos."""
import os
from pathlib import Path
import stat
import sys
import tempfile

from taxiroute.oauth_gmail_contenedor import _MAX_BYTES, _validar_contenido

DIRECTORIO = Path("/run/taxiroute")
NOMBRE = "gmail_oauth.json"


def cargar(entrada, directorio=DIRECTORIO):
    """Publica el archivo completo sin reemplazar uno existente.

    El directorio debe ser privado y estar preparado por el entorno. Se usa
    un enlace exclusivo para evitar que un lector vea un archivo incompleto.
    """
    temporal = None
    try:
        datos = entrada.read(_MAX_BYTES + 1)
        _validar_contenido(datos)
        directorio = Path(directorio)
        info = directorio.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError
        if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError
        descriptor, temporal = tempfile.mkstemp(prefix=".oauth-", dir=directorio)
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(datos)
        os.chmod(temporal, 0o600)
        os.link(temporal, directorio / NOMBRE, follow_symlinks=False)
    except Exception:
        raise ValueError("No se ha podido cargar la autorización temporal.") from None
    finally:
        if temporal is not None:
            Path(temporal).unlink(missing_ok=True)


def main():
    try:
        cargar(sys.stdin.buffer)
    except ValueError:
        print("No se ha podido cargar la autorización temporal.", file=sys.stderr)
        return 1
    print("Autorización temporal cargada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
