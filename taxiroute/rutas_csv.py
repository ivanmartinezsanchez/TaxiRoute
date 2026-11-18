"""Resolución interna del historial CSV perteneciente a una cuenta."""
from uuid import UUID

from taxiroute.historial import HistorialCSV


def _historial_de_cuenta(carpeta, usuario):
    """Valida el identificador y rechaza enlaces en los archivos de la cuenta."""
    try:
        if not isinstance(usuario, str) or str(UUID(usuario)) != usuario:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Identificador de cuenta no válido para el historial.") from None
    directorio = carpeta / usuario
    archivo = directorio / "historial.csv"
    if directorio.is_symlink() or archivo.is_symlink() or (directorio / "historial.csv.lock").is_symlink():
        raise ValueError("El historial no puede usar enlaces simbólicos.")
    if directorio.exists() and not directorio.is_dir():
        raise ValueError("El directorio de historial no es válido.")
    return HistorialCSV(archivo)
