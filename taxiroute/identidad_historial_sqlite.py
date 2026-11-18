"""Identidad experimental de un historial SQLite ya inicializado.

La identidad distingue instalaciones distintas; no detecta copias antiguas
ni alteraciones deliberadas. No cambia el vínculo activo en la base de cuentas.
"""
from contextlib import closing
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from taxiroute.historial_sqlite import HistorialSQLite


class IdentidadHistorialError(ValueError):
    """La identidad del historial está ausente o necesita revisión."""


def _ruta_existente(ruta):
    ruta = Path(ruta)
    if ruta.is_symlink() or not ruta.is_file():
        raise IdentidadHistorialError("El historial SQLite debe existir y no ser un enlace.")
    return ruta.resolve()


def _leer(conexion):
    tabla = conexion.execute("SELECT type FROM sqlite_schema WHERE name='identidad_historial'").fetchone()
    if tabla is None:
        return None
    if tabla[0] != 'table':
        raise IdentidadHistorialError("La identidad del historial no es válida.")
    columnas = conexion.execute('PRAGMA table_info(identidad_historial)').fetchall()
    if [c[1] for c in columnas] != ['id', 'identificador']:
        raise IdentidadHistorialError("La estructura de la identidad no es válida.")
    filas = conexion.execute('SELECT id, identificador FROM identidad_historial').fetchall()
    if len(filas) != 1 or filas[0][0] != 1:
        raise IdentidadHistorialError("La identidad del historial necesita revisión.")
    valor = filas[0][1]
    try:
        if not isinstance(valor, str) or str(UUID(valor)) != valor:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise IdentidadHistorialError("La identidad del historial no es válida.") from None
    return valor


def leer_identidad_historial(ruta):
    """Lee la identidad o informa de que aún no se ha preparado; no crea archivos."""
    ruta = _ruta_existente(ruta)
    with closing(sqlite3.connect(ruta.as_uri() + '?mode=ro', uri=True, isolation_level=None)) as conexion:
        conexion.execute('BEGIN')
        HistorialSQLite._verificar(conexion, permitir_creacion=False)
        return _leer(conexion)


def _preparar_en_transaccion(conexion):
    """Prepara o verifica la identidad bajo una transacción SQLite existente."""
    existente = _leer(conexion)
    if existente is not None:
        return existente
    identificador = str(uuid4())
    conexion.execute('''CREATE TABLE identidad_historial (
        id INTEGER PRIMARY KEY CHECK(id=1),
        identificador TEXT NOT NULL CHECK(length(identificador)=36)
    ) STRICT''')
    conexion.execute('INSERT INTO identidad_historial (id, identificador) VALUES (1, ?)', (identificador,))
    if _leer(conexion) != identificador:
        raise IdentidadHistorialError("No se ha podido confirmar la identidad.")
    return identificador


def _leer_procedencia(conexion):
    """Lee la instalación CSV que preparó este destino, si existe."""
    tabla = conexion.execute("SELECT type FROM sqlite_schema WHERE name='procedencia_migracion_csv'").fetchone()
    if tabla is None:
        return None
    if tabla[0] != 'table':
        raise IdentidadHistorialError("La procedencia del historial no es válida.")
    columnas = conexion.execute('PRAGMA table_info(procedencia_migracion_csv)').fetchall()
    if [c[1] for c in columnas] != ['id', 'vinculo_csv', 'identidad_sqlite']:
        raise IdentidadHistorialError("La estructura de procedencia no es válida.")
    filas = conexion.execute('SELECT id, vinculo_csv, identidad_sqlite FROM procedencia_migracion_csv').fetchall()
    if len(filas) != 1 or filas[0][0] != 1:
        raise IdentidadHistorialError("La procedencia del historial necesita revisión.")
    origen, identidad = filas[0][1], filas[0][2]
    try:
        if any(not isinstance(x, str) or str(UUID(x)) != x for x in (origen, identidad)):
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise IdentidadHistorialError("La procedencia del historial no es válida.") from None
    if _leer(conexion) != identidad:
        raise IdentidadHistorialError("La procedencia no coincide con la identidad SQLite.")
    return origen, identidad


def _preparar_procedencia_en_transaccion(conexion, vinculo_csv, identidad_sqlite, *, identidad_preexistente):
    """Vincula el destino a su origen, o verifica un reintento del mismo."""
    esperada = (vinculo_csv, identidad_sqlite)
    procedencia = _leer_procedencia(conexion)
    if procedencia is not None:
        if procedencia != esperada:
            raise IdentidadHistorialError("El historial SQLite pertenece a otra instalación.")
        return
    if identidad_preexistente:
        raise IdentidadHistorialError("La identidad SQLite no tiene procedencia verificable.")
    conexion.execute("""CREATE TABLE procedencia_migracion_csv (
        id INTEGER PRIMARY KEY CHECK(id=1), vinculo_csv TEXT NOT NULL,
        identidad_sqlite TEXT NOT NULL
    ) STRICT""")
    conexion.execute('INSERT INTO procedencia_migracion_csv VALUES (1, ?, ?)', esperada)
    if _leer_procedencia(conexion) != esperada:
        raise IdentidadHistorialError("No se ha podido confirmar la procedencia SQLite.")


def preparar_identidad_historial(ruta):
    """Prepara una identidad una sola vez, sin borrar ni cambiar las carreras."""
    ruta = _ruta_existente(ruta)
    with closing(sqlite3.connect(ruta.as_uri() + '?mode=rw', uri=True, timeout=5, isolation_level=None)) as conexion:
        conexion.execute('BEGIN IMMEDIATE')
        try:
            HistorialSQLite._verificar(conexion, permitir_creacion=False)
            identificador = _preparar_en_transaccion(conexion)
            conexion.commit()
            return identificador
        except BaseException:
            conexion.rollback()
            raise
