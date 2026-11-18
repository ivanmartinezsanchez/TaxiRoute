"""Vínculo experimental entre cuentas nuevas y su carpeta de historiales CSV.

No migra ni identifica automáticamente instalaciones antiguas. El marcador
evita confusiones accidentales, no protege frente a copias o cambios hostiles.
"""
from contextlib import closing
import os
from pathlib import Path
from uuid import UUID, uuid4

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.conciliacion_csv import _conectar_existente

MARCADOR_HISTORIAL = "IDENTIDAD_TAXIROUTE.txt"


class VinculoHistorialError(ValueError):
    """La identificación del historial necesita revisión."""


def _validar_base(conexion):
    if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
            or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
        raise VinculoHistorialError("La base de cuentas no es compatible.")


def _carpeta_valida(carpeta):
    carpeta = Path(carpeta)
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise VinculoHistorialError("La carpeta de historiales no está disponible.")
    return carpeta.resolve()


def _registro(conexion):
    _validar_base(conexion)
    return conexion.execute("SELECT formato, identificador, estado FROM vinculo_historial WHERE id=1").fetchone()


def crear_vinculo_csv_nuevo(ruta_cuentas, carpeta_historial):
    """Crea un vínculo solo para cuentas e historial nuevos y vacíos.

    La operación tiene tres pasos durables: reservar en cuentas, crear el
    marcador exclusivo y confirmar. Un fallo deja 'preparando' y exige revisión;
    no elimina ni adopta datos. El llamador debe controlar la preparación.
    """
    base = Path(ruta_cuentas)
    carpeta = _carpeta_valida(carpeta_historial)
    if base.is_symlink() or not base.is_file() or any(carpeta.iterdir()):
        raise VinculoHistorialError("La preparación necesita archivos nuevos y un historial vacío.")
    identificador = str(uuid4())
    with closing(_conectar_existente(base)) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            if _registro(conexion) is not None:
                raise VinculoHistorialError("La base ya tiene un vínculo de historial.")
            for tabla in ("usuarios", "carreras_recuperacion", "intenciones_operacion", "recibos_operacion"):
                if conexion.execute(f"SELECT 1 FROM {tabla} LIMIT 1").fetchone():
                    raise VinculoHistorialError("Solo se pueden vincular bases nuevas y vacías.")
            conexion.execute("INSERT INTO vinculo_historial VALUES (1, 'csv', ?, 'preparando')", (identificador,))
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise
    marcador = carpeta / MARCADOR_HISTORIAL
    with marcador.open("x", encoding="ascii", newline="\n") as archivo:
        archivo.write(identificador + "\n")
        archivo.flush()
        os.fsync(archivo.fileno())
    with closing(_conectar_existente(base)) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            registro = _registro(conexion)
            if registro is None or tuple(registro) != ("csv", identificador, "preparando"):
                raise VinculoHistorialError("El vínculo de preparación ha cambiado.")
            if marcador.is_symlink() or not marcador.is_file() or marcador.read_bytes() != (identificador + "\n").encode("ascii"):
                raise VinculoHistorialError("El marcador del historial ha cambiado.")
            actualizado = conexion.execute("UPDATE vinculo_historial SET estado='listo' WHERE id=1 AND estado='preparando'")
            if actualizado.rowcount != 1:
                raise VinculoHistorialError("No se ha podido confirmar el vínculo.")
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise
    return identificador


def verificar_vinculo_csv(ruta_cuentas, carpeta_historial):
    """Valida vínculo CSV existente. False indica instalación antigua sin vínculo."""
    carpeta = _carpeta_valida(carpeta_historial)
    base = Path(ruta_cuentas)
    if base.is_symlink() or not base.is_file():
        raise VinculoHistorialError("La base de cuentas no está disponible.")
    marcador = carpeta / MARCADOR_HISTORIAL
    with closing(_conectar_existente(base)) as conexion:
        registro = _registro(conexion)
    if registro is None:
        if marcador.exists() or marcador.is_symlink():
            raise VinculoHistorialError("El historial está vinculado a otra instalación.")
        return False
    formato, identificador, estado = registro
    try:
        valido = isinstance(identificador, str) and str(UUID(identificador)) == identificador
    except (ValueError, AttributeError):
        valido = False
    if formato != "csv" or estado != "listo" or not valido:
        raise VinculoHistorialError("El vínculo del historial necesita revisión.")
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_bytes() != (identificador + "\n").encode("ascii"):
        raise VinculoHistorialError("El historial no corresponde a esta instalación.")
    return True


def rechazar_base_vinculada(ruta_cuentas):
    """Impide usar una base CSV vinculada en la composición SQLite antigua."""
    base = Path(ruta_cuentas)
    if base.is_symlink() or not base.is_file():
        raise VinculoHistorialError("La base de cuentas no está disponible.")
    with closing(_conectar_existente(base)) as conexion:
        registro = _registro(conexion)
    if registro is not None:
        raise VinculoHistorialError("La base tiene un historial vinculado y no admite esta composición SQLite.")
