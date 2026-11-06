"""Coordinación de acceso al historial entre procesos de TaxiRoute."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
from time import monotonic, sleep


class HistorialOcupado(TimeoutError):
    """No se obtuvo el bloqueo dentro del tiempo previsto."""


@contextmanager
def bloqueo_historial(ruta: Path, espera: float = 2.0):
    """Bloquea un archivo auxiliar; no debe eliminarse durante su uso."""
    ruta = Path(ruta).resolve()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    auxiliar = ruta.with_name(ruta.name + ".lock")
    with auxiliar.open("a+b") as archivo:
        limite = monotonic() + espera
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    archivo.seek(0)
                    msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl
                    fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise NotImplementedError("Sistema no compatible con el bloqueo del historial")
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(error, "winerror", None) not in (32, 33, 36):
                    raise
                restante = limite - monotonic()
                if restante <= 0:
                    raise HistorialOcupado("El historial está siendo utilizado por otra ejecución") from error
                sleep(min(0.05, restante))
        try:
            yield
        finally:
            if os.name == "nt":
                archivo.seek(0)
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
