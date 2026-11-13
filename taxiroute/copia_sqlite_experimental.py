"""Copias SQLite aisladas para instalaciones detenidas; no es restauración en producción.

Las dos bases se copian por separado bajo el bloqueo cooperativo del servidor.
El llamador debe detener también cualquier proceso no cooperativo, que no está
cubierto por el bloqueo. Las copias contienen información privada y requieren
protección del sistema operativo. No se detectan todas las copias antiguas.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.configuracion import cargar_tarifas
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado

MARCADOR = "COPIA_SQLITE_EXPERIMENTAL.txt"
_CONTENIDO = "TaxiRoute: copia SQLite experimental completa, versión 1.\n"


class CopiaSQLiteError(ValueError):
    """La copia o restauración requiere revisión."""


def _archivos(carpeta):
    carpeta = Path(carpeta)
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise CopiaSQLiteError("La carpeta de origen no está disponible.")
    archivos = tuple(carpeta / nombre for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"))
    if any(archivo.is_symlink() or not archivo.is_file() for archivo in archivos):
        raise CopiaSQLiteError("Faltan archivos de la instalación o son enlaces.")
    if len({archivo.resolve() for archivo in archivos}) != 3:
        raise CopiaSQLiteError("Los archivos deben ser diferentes.")
    return archivos


def _validar(carpeta):
    cuentas, historial, tarifas = _archivos(carpeta)
    cargar_tarifas(tarifas)
    for ruta in (cuentas, historial):
        with closing(sqlite3.connect(ruta.resolve().as_uri() + "?mode=ro", uri=True)) as conexion:
            if conexion.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise CopiaSQLiteError("Una base no ha superado la comprobación de integridad.")
    verificar_historial_sqlite_vinculado(cuentas, historial)
    with closing(sqlite3.connect(cuentas.resolve().as_uri() + "?mode=ro", uri=True)) as conexion:
        if (conexion.execute("SELECT 1 FROM carreras_recuperacion WHERE estado_registro != 'guardada' LIMIT 1").fetchone()
                or conexion.execute("SELECT 1 FROM intenciones_operacion LIMIT 1").fetchone()):
            raise CopiaSQLiteError("Hay carreras u operaciones pendientes de revisión.")


def _copiar_base(origen, destino):
    with closing(sqlite3.connect(origen.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destino)) as dst:
            src.backup(dst)


def _preparar_destino(origen, destino):
    origen = Path(origen)
    _archivos(origen)
    destino = Path(destino).expanduser()
    if destino.name in ("", ".", ".."):
        raise CopiaSQLiteError("Selecciona una carpeta nueva.")
    padre = destino.parent.resolve(strict=True)
    origen_resuelto = origen.resolve(strict=True)
    if padre == origen_resuelto or origen_resuelto in padre.parents:
        raise CopiaSQLiteError("El destino debe estar fuera de la carpeta de origen.")
    if any((ruta / ".git").exists() or (ruta / ".git").is_symlink() for ruta in (padre, *padre.parents)):
        raise CopiaSQLiteError("El destino debe estar fuera de un repositorio Git.")
    return origen_resuelto, padre / destino.name


def _copiar_archivos(origen, destino):
    cuentas, historial, tarifas = _archivos(origen)
    _copiar_base(cuentas, destino / "cuentas.sqlite3")
    _copiar_base(historial, destino / "historial.sqlite3")
    with tarifas.open("rb") as src, (destino / "tarifas.json").open("xb") as dst:
        while bloque := src.read(65536):
            dst.write(bloque)


def crear_copia_sqlite(origen, destino):
    """Crea carpeta nueva y marcador solo tras validar ambas bases y las tarifas.

    La aplicación debe estar detenida. El bloqueo excluye solo procesos que lo
    respeten, y no garantiza exclusión entre Windows y Docker Desktop. Si falla,
    se conserva cualquier carpeta incompleta sin marcador para revisión.
    """
    origen, destino = _preparar_destino(origen, destino)
    with bloqueo_historial(origen / "servidor", espera=0):
        _validar(origen)
        destino.mkdir(mode=0o700)
        _copiar_archivos(origen, destino)
        _validar(destino)
        with (destino / MARCADOR).open("x", encoding="utf-8", newline="\n") as archivo:
            archivo.write(_CONTENIDO)
    return destino


def restaurar_copia_sqlite(copia, destino):
    """Restaura solo en carpeta nueva y revoca sesiones y autorizaciones antiguas.

    Es una recuperación aislada experimental: no sustituye la instalación
    original ni debe utilizarse en paralelo con ella. Invalida también los
    códigos de recuperación, que requieren un procedimiento posterior. Las
    contraseñas, bloqueos y permisos pueden ser antiguos y requieren revisión.
    """
    copia, destino = _preparar_destino(copia, destino)
    marcador = copia / MARCADOR
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != _CONTENIDO:
        raise CopiaSQLiteError("La copia no está marcada como completa.")
    with bloqueo_historial(copia / "servidor", espera=0):
        _validar(copia)
        destino.mkdir(mode=0o700)
        _copiar_archivos(copia, destino)
        cuentas = destino / "cuentas.sqlite3"
        fecha = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(cuentas)) as conexion:
            with conexion:
                conexion.execute("UPDATE sesiones SET revocada_en=? WHERE revocada_en IS NULL", (fecha,))
                conexion.execute("UPDATE desafios_acceso SET invalidado_en=? WHERE invalidado_en IS NULL", (fecha,))
                conexion.execute("UPDATE autorizaciones_cambio SET consumido_en=? WHERE consumido_en IS NULL", (fecha,))
                conexion.execute("UPDATE codigos_recuperacion SET invalidado_en=? WHERE invalidado_en IS NULL", (fecha,))
        _validar(destino)
    return destino
