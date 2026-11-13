"""Ensayo aislado de restauración con sustitución de códigos.

No sustituye una instalación en uso ni valida el estado más reciente de una
copia antigua. El operador debe proteger físicamente la copia y su destino.
"""
from contextlib import closing
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3

from argon2.exceptions import InvalidHashError, VerificationError
from taxiroute.codigos_recuperacion_experimentales import _crear_lote, _verificador, _FORMATO
from taxiroute.copia_sqlite_experimental import (
    CopiaSQLiteError, MARCADOR, _CONTENIDO, _preparar_destino, _validar, _copiar_archivos,
)
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cuentas import _PROTECTOR, normalizar_usuario


def _administrador(conexion, nombre, contrasena, codigo):
    """Exige contraseña y un código vigente en la copia, sin desbloquearla."""
    try:
        _, nombre = normalizar_usuario(nombre)
        if not isinstance(contrasena, str) or len(contrasena) > 128:
            raise ValueError()
        if not isinstance(codigo, str) or not _FORMATO.fullmatch(codigo):
            raise ValueError()
        fila = conexion.execute("""SELECT id, hash_contrasena FROM usuarios
            WHERE nombre_usuario_normalizado=? AND rol='administrador' AND habilitado=1""",
            (nombre,)).fetchone()
        if fila is None or not _PROTECTOR.verify(fila[1], contrasena):
            raise ValueError()
        valido = conexion.execute("""SELECT 1 FROM codigos_recuperacion
            WHERE usuario_id=? AND verificador=? AND usado_en IS NULL
              AND invalidado_en IS NULL""", (fila[0], _verificador(fila[0], codigo))).fetchone()
        if valido is None:
            raise ValueError()
        return fila[0]
    except (ValueError, TypeError, InvalidHashError, VerificationError):
        raise CopiaSQLiteError("No se pudo verificar el administrador de la copia.") from None


def _huella_estado(carpeta):
    """Compara datos lógicos, sin imprimir ni guardar su contenido o huella."""
    huella = hashlib.sha256()
    for nombre in ("cuentas.sqlite3", "historial.sqlite3"):
        ruta = carpeta / nombre
        with closing(sqlite3.connect(ruta.resolve().as_uri() + "?mode=ro", uri=True)) as conexion:
            conexion.execute("BEGIN")
            for linea in conexion.iterdump():
                datos = linea.encode("utf-8")
                huella.update(len(datos).to_bytes(8, "big"))
                huella.update(datos)
    huella.update((carpeta / "tarifas.json").read_bytes())
    return huella.digest()


def restaurar_con_codigos(copia, destino, *, estado_actual, usuario, contrasena, codigo, confirmar_codigos):
    """Restaura aislada y cambia los códigos tras comprobar los de la copia.

    Los códigos se entregan al confirmador antes de guardar; solo son válidos
    cuando esta función retorna. Un fallo puede dejar una carpeta incompleta.
    La copia origen permanece intacta. Exige comparar con una instalación
    actual detenida; ambas deben permanecer aisladas de procesos externos.
    """
    if not callable(confirmar_codigos):
        raise CopiaSQLiteError("Es necesario confirmar los nuevos códigos.")
    if os.name != "nt":
        raise CopiaSQLiteError("Este ensayo solo admite Windows.")
    copia, destino = _preparar_destino(copia, destino)
    actual = Path(estado_actual).resolve(strict=True)
    if actual == copia or destino == actual or actual in destino.parents:
        raise CopiaSQLiteError("Se necesitan instalaciones separadas y un destino nuevo.")
    marcador = copia / MARCADOR
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != _CONTENIDO:
        raise CopiaSQLiteError("La copia no está marcada como completa.")
    with ExitStack() as pila:
        for carpeta in sorted((copia, actual), key=lambda ruta: str(ruta).casefold()):
            pila.enter_context(bloqueo_historial(carpeta / "servidor", espera=0))
        _validar(copia)
        _validar(actual)
        if _huella_estado(copia) != _huella_estado(actual):
            raise CopiaSQLiteError("La copia y la instalación actual no coinciden; requiere revisión.")
        cuentas = copia / "cuentas.sqlite3"
        with closing(sqlite3.connect(cuentas.resolve().as_uri() + "?mode=ro", uri=True)) as conexion:
            admin = _administrador(conexion, usuario, contrasena, codigo)
        nuevos = _crear_lote(5)
        if confirmar_codigos(nuevos) is not True:
            raise CopiaSQLiteError("Restauración cancelada; los nuevos códigos no están activos.")
        if _huella_estado(copia) != _huella_estado(actual):
            raise CopiaSQLiteError("El estado ha cambiado; requiere revisión.")
        temporal = destino.with_name(destino.name + ".incompleta-" + secrets.token_hex(8))
        temporal.mkdir(mode=0o700)
        _copiar_archivos(copia, temporal)
        _validar(temporal)
        if _huella_estado(temporal) != _huella_estado(actual):
            raise CopiaSQLiteError("El estado ha cambiado durante la copia; requiere revisión.")
        fecha = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(temporal / "cuentas.sqlite3", isolation_level=None)) as conexion:
            conexion.execute("PRAGMA foreign_keys=ON")
            conexion.execute("BEGIN IMMEDIATE")
            try:
                if _administrador(conexion, usuario, contrasena, codigo) != admin:
                    raise CopiaSQLiteError("La cuenta de la copia ha cambiado; requiere revisión.")
                conexion.execute("UPDATE sesiones SET revocada_en=? WHERE revocada_en IS NULL", (fecha,))
                conexion.execute("UPDATE desafios_acceso SET invalidado_en=? WHERE invalidado_en IS NULL", (fecha,))
                conexion.execute("UPDATE autorizaciones_cambio SET consumido_en=? WHERE consumido_en IS NULL", (fecha,))
                conexion.execute("UPDATE codigos_recuperacion SET invalidado_en=? WHERE invalidado_en IS NULL", (fecha,))
                conexion.executemany("""INSERT INTO codigos_recuperacion
                    (usuario_id, verificador, creado_en) VALUES (?, ?, ?)""",
                    [(admin, _verificador(admin, valor), fecha) for valor in nuevos])
                conexion.commit()
            except BaseException:
                if conexion.in_transaction:
                    conexion.rollback()
                raise
        _validar(temporal)
        # En Windows rename no reemplaza un destino existente. El directorio
        # incompleto se conserva para revisión si falla cualquier paso.
        os.rename(temporal, destino)
    return destino
