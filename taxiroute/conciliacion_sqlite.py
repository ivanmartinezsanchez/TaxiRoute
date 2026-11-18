"""Conciliación experimental entre recuperación y un historial SQLite separado.

No autentica al llamador ni modifica carreras en memoria. Las dos bases no
comparten una transacción atómica; un corte puede requerir un reintento.
"""
from contextlib import closing
from pathlib import Path
import os
import sqlite3

from taxiroute.conciliacion_csv import _conectar_existente, _identificadores, _leer
from taxiroute.historial import CAMPOS, _fila
from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError


class ConciliacionSQLiteError(ValueError):
    """La carrera o el historial no permiten confirmar la operación."""


def _abrir_historial(ruta):
    try:
        conexion = sqlite3.connect(ruta.resolve().as_uri() + "?mode=rw", uri=True,
                                  timeout=5, isolation_level=None)
    except sqlite3.Error as error:
        raise HistorialSQLiteError("No se puede abrir el historial SQLite existente.") from error
    conexion.row_factory = sqlite3.Row
    return conexion


def _comprobar_fila(conexion, esperada, usuario_id, *, permitir_insertar):
    columnas = ", ".join(CAMPOS)
    anterior = conexion.execute(
        f"SELECT {columnas}, usuario_id FROM carreras WHERE identificador=?",
        (esperada["identificador"],),
    ).fetchone()
    if anterior is None:
        if not permitir_insertar:
            raise ConciliacionSQLiteError("La carrera no aparece en el historial.")
        marcadores = ", ".join("?" for _ in CAMPOS)
        conexion.execute(
            f"INSERT INTO carreras ({columnas}, usuario_id) VALUES ({marcadores}, ?)",
            (*tuple(esperada[campo] for campo in CAMPOS), usuario_id),
        )
        # Reconsultar incluso después de insertar: detecta triggers que alteren
        # o ignoren la fila antes de confirmar el historial.
        return _comprobar_fila(conexion, esperada, usuario_id, permitir_insertar=False)
    if (anterior["usuario_id"] != usuario_id
            or {campo: anterior[campo] for campo in CAMPOS} != esperada):
        raise ConciliacionSQLiteError("La carrera no coincide con el historial del usuario.")


def _preparar_historial(base, destino, usuario_id, carrera_id, datos, esperada):
    # Siempre historial antes de recuperación; nunca invertir este orden.
    with closing(_abrir_historial(destino)) as historial:
        historial.execute("BEGIN IMMEDIATE")
        try:
            HistorialSQLite._verificar(historial, permitir_creacion=False)
            with closing(_conectar_existente(base)) as recuperacion:
                recuperacion.execute("BEGIN IMMEDIATE")
                try:
                    actuales, estado, _ = _leer(recuperacion, usuario_id, carrera_id)
                    if actuales != datos:
                        raise ConciliacionSQLiteError("El resumen cambió durante la conciliación.")
                    _comprobar_fila(historial, esperada, usuario_id,
                                    permitir_insertar=estado == "guardado_pendiente")
                    historial.commit()
                finally:
                    recuperacion.rollback()
        except BaseException:
            historial.rollback()
            raise


def _confirmar(base, destino, usuario_id, carrera_id, datos, esperada):
    with closing(_abrir_historial(destino)) as historial:
        historial.execute("BEGIN IMMEDIATE")
        try:
            HistorialSQLite._verificar(historial, permitir_creacion=False)
            with closing(_conectar_existente(base)) as recuperacion:
                recuperacion.execute("BEGIN IMMEDIATE")
                try:
                    actuales, estado, _ = _leer(recuperacion, usuario_id, carrera_id)
                    if actuales != datos:
                        raise ConciliacionSQLiteError("El resumen cambió durante la conciliación.")
                    _comprobar_fila(historial, esperada, usuario_id, permitir_insertar=False)
                    if estado == "guardado_pendiente":
                        actualizado = recuperacion.execute(
                            """UPDATE carreras_recuperacion SET estado_registro='guardada'
                               WHERE id=? AND usuario_id=? AND estado_registro='guardado_pendiente'""",
                            (carrera_id, usuario_id))
                        if actualizado.rowcount != 1:
                            raise ConciliacionSQLiteError("No se pudo confirmar la carrera guardada.")
                    recuperacion.commit()
                    return estado == "guardada"
                except BaseException:
                    recuperacion.rollback()
                    raise
        finally:
            historial.rollback()


def conciliar_pendiente_sqlite(ruta_base, ruta_historial, usuario_id, carrera_id):
    """Confirma solo tras verificar el historial bloqueado; permite reintento.

    Las escrituras SQLite respetan los bloqueos, pero sustituir archivos desde
    fuera de SQLite o modificarlos tras confirmar queda fuera de esta garantía.
    """
    _identificadores(usuario_id, carrera_id)
    base, destino = Path(ruta_base), Path(ruta_historial)
    if base.resolve() == destino.resolve() or (base.exists() and destino.exists() and os.path.samefile(base, destino)):
        raise ConciliacionSQLiteError("Las bases de recuperación e historial deben ser distintas.")
    with closing(_conectar_existente(base)) as conexion:
        datos, _, resumen = _leer(conexion, usuario_id, carrera_id)
    esperada = _fila(resumen)
    _preparar_historial(base, destino, usuario_id, carrera_id, datos, esperada)
    return _confirmar(base, destino, usuario_id, carrera_id, datos, esperada)
