"""Historial relacional independiente con el mismo contrato de lectura que CSV.

El archivo SQLite pertenece exclusivamente a este historial. Los importes y
las duraciones se guardan como texto decimal para conservar su precisión.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from taxiroute.historial import CAMPOS, _fila


_ID_APLICACION = 0x54584853
_VERSION = 2
_COLUMNAS = ", ".join(CAMPOS)


class HistorialSQLiteError(OSError):
    """No se puede leer o guardar el historial relacional."""


class HistorialSQLite:
    """Vista por propietario; el llamador debe validar la sesión y el usuario.

    None identifica exclusivamente carreras sin propietario; no muestra todas.
    En modo exigir_existente, consultar y guardar no crean una base ausente.
    Inicializar es una operación explícita, no una comprobación de arranque.
    No abre ni migra el historial CSV ni valida identidades contra otra base.
    """

    def __init__(self, ruta, *, usuario_id=None, exigir_existente=False):
        if usuario_id is not None and (
            not isinstance(usuario_id, str) or not usuario_id.strip()
            or usuario_id != usuario_id.strip()
        ):
            raise ValueError("Identificador de usuario no válido.")
        if type(exigir_existente) is not bool:
            raise ValueError("exigir_existente debe ser verdadero o falso.")
        self.ruta = Path(ruta)
        self.usuario_id = usuario_id
        self.exigir_existente = exigir_existente

    @staticmethod
    def _verificar(conexion, *, permitir_creacion):
        version = conexion.execute("PRAGMA user_version").fetchone()[0]
        identidad = conexion.execute("PRAGMA application_id").fetchone()[0]
        if version == 0 and identidad == 0 and permitir_creacion:
            objetos = conexion.execute("SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if objetos:
                raise HistorialSQLiteError("La base sin identificar no está vacía.")
            conexion.execute("""CREATE TABLE carreras (
                orden INTEGER PRIMARY KEY,
                identificador TEXT NOT NULL UNIQUE CHECK(length(identificador)>0),
                inicio TEXT NOT NULL, fin TEXT NOT NULL, duracion_segundos TEXT NOT NULL,
                segundos_parado TEXT NOT NULL, segundos_movimiento TEXT NOT NULL,
                tarifa_parado TEXT NOT NULL, tarifa_movimiento TEXT NOT NULL,
                importe TEXT NOT NULL,
                usuario_id TEXT CHECK(usuario_id IS NULL OR length(trim(usuario_id)) > 0)
            ) STRICT""")
            conexion.execute(f"PRAGMA application_id = {_ID_APLICACION}")
            conexion.execute(f"PRAGMA user_version = {_VERSION}")
        elif version != _VERSION or identidad != _ID_APLICACION:
            raise HistorialSQLiteError("La base no es un historial TaxiRoute compatible.")
        columnas = conexion.execute("PRAGMA table_info(carreras)").fetchall()
        if [fila[1] for fila in columnas] != ["orden", *CAMPOS, "usuario_id"]:
            raise HistorialSQLiteError("La estructura del historial necesita revisión.")

    def inicializar(self):
        """Crea explícitamente el esquema o valida uno existente sin borrar datos."""
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(str(self.ruta), timeout=5, isolation_level=None)) as conexion:
                conexion.execute("BEGIN IMMEDIATE")
                try:
                    self._verificar(conexion, permitir_creacion=True)
                    conexion.commit()
                except BaseException:
                    conexion.rollback()
                    raise
        except sqlite3.Error as error:
            raise HistorialSQLiteError("No se puede inicializar el historial SQLite.") from error
    def consultar(self):
        if not self.ruta.exists():
            if self.exigir_existente:
                raise HistorialSQLiteError("No se encuentra el historial SQLite existente.")
            return []
        try:
            uri = self.ruta.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)) as conexion:
                conexion.row_factory = sqlite3.Row
                conexion.execute("BEGIN")
                self._verificar(conexion, permitir_creacion=False)
                filas = conexion.execute(
                    f"SELECT {_COLUMNAS} FROM carreras WHERE usuario_id IS ? ORDER BY orden",
                    (self.usuario_id,),
                ).fetchall()
                return [dict(fila) for fila in filas]
        except sqlite3.Error as error:
            raise HistorialSQLiteError("No se puede leer el historial SQLite.") from error

    def guardar(self, resumen):
        try:
            fila = _fila(resumen)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("El resumen de carrera no es válido.") from error
        if any(not fila[campo] for campo in CAMPOS):
            raise ValueError("El resumen de carrera está incompleto.")
        try:
            if self.exigir_existente:
                objetivo = self.ruta.resolve().as_uri() + "?mode=rw"
            else:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                objetivo = str(self.ruta)
            with closing(sqlite3.connect(objetivo, uri=self.exigir_existente, timeout=5, isolation_level=None)) as conexion:
                conexion.row_factory = sqlite3.Row
                conexion.execute("BEGIN IMMEDIATE")
                try:
                    self._verificar(conexion, permitir_creacion=not self.exigir_existente)
                    anterior = conexion.execute(
                        f"SELECT {_COLUMNAS}, usuario_id FROM carreras WHERE identificador=?",
                        (fila["identificador"],),
                    ).fetchone()
                    if anterior is not None:
                        if (anterior["usuario_id"] != self.usuario_id
                                or {campo: anterior[campo] for campo in CAMPOS} != fila):
                            raise ValueError("La carrera ya existe con datos diferentes")
                    else:
                        marcadores = ", ".join("?" for _ in CAMPOS)
                        conexion.execute(
                            f"INSERT INTO carreras ({_COLUMNAS}, usuario_id) VALUES ({marcadores}, ?)",
                            (*tuple(fila[campo] for campo in CAMPOS), self.usuario_id),
                        )
                    conexion.commit()
                except BaseException:
                    conexion.rollback()
                    raise
        except sqlite3.Error as error:
            raise HistorialSQLiteError("No se puede guardar el historial SQLite.") from error