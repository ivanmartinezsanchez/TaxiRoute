"""Migración experimental no destructiva de CSV a historial SQLite.

Las filas históricas quedan sin propietario. No elimina ni altera el CSV y no
incluye una interfaz de usuario; quien la integre debe gestionar permisos,
respaldos, coordinación con la aplicación y cualquier error comunicado.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import os
from pathlib import Path
import re
import sqlite3

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.historial import CAMPOS, HistorialCSV
from taxiroute.historial_sqlite import HistorialSQLite, HistorialSQLiteError


class MigracionHistorialError(ValueError):
    """El CSV o un conflicto impiden completar la migración."""


@dataclass(frozen=True)
class ResultadoMigracion:
    filas_origen: int
    insertadas: int
    ya_existentes: int


def _numero(valor, campo):
    # Decimal de Python puede usar notación exponencial para cantidades pequeñas.
    if (not isinstance(valor, str) or len(valor) > 64
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?", valor)):
        raise MigracionHistorialError(f"El campo {campo} no contiene un decimal válido.")
    try:
        numero = Decimal(valor)
    except InvalidOperation as error:
        raise MigracionHistorialError(f"El campo {campo} no contiene un decimal válido.") from error
    if not numero.is_finite() or numero < 0 or not -50 <= numero.as_tuple().exponent <= 50:
        raise MigracionHistorialError(f"El campo {campo} está fuera de los límites de migración.")
    return numero


def _validar_fila(fila):
    if not isinstance(fila, dict) or set(fila) != set(CAMPOS):
        raise MigracionHistorialError("La fila no tiene los campos esperados.")
    identificador = fila["identificador"]
    if (not isinstance(identificador, str) or not identificador.strip()
            or identificador != identificador.strip() or len(identificador) > 128):
        raise MigracionHistorialError("El identificador de carrera no es válido.")
    try:
        inicio = datetime.fromisoformat(fila["inicio"])
        fin = datetime.fromisoformat(fila["fin"])
        if inicio.tzinfo is None or inicio.utcoffset() is None or fin.tzinfo is None or fin.utcoffset() is None:
            raise ValueError("Falta zona horaria")
        if fin.astimezone(timezone.utc) < inicio.astimezone(timezone.utc):
            raise ValueError("La fecha final es anterior al inicio")
    except (TypeError, ValueError, OverflowError) as error:
        raise MigracionHistorialError("Las fechas de una carrera no son válidas.") from error
    campos = ("duracion_segundos", "segundos_parado", "segundos_movimiento",
              "tarifa_parado", "tarifa_movimiento", "importe")
    numeros = {campo: _numero(fila[campo], campo) for campo in campos}
    try:
        with localcontext() as contexto:
            contexto.prec = 160
            if numeros["duracion_segundos"] != numeros["segundos_parado"] + numeros["segundos_movimiento"]:
                raise MigracionHistorialError("La duración no coincide con los tiempos de la carrera.")
            total = (numeros["segundos_parado"] * numeros["tarifa_parado"]
                     + numeros["segundos_movimiento"] * numeros["tarifa_movimiento"])
            if total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != numeros["importe"]:
                raise MigracionHistorialError("El importe no coincide con los tiempos y las tarifas.")
    except InvalidOperation as error:
        raise MigracionHistorialError("No se puede validar el importe de la carrera.") from error


def migrar_historial_csv(ruta_csv, ruta_sqlite) -> ResultadoMigracion:
    """Migra todas las filas en una transacción SQLite, sin modificar el CSV.

    Los escritores del CSV deben respetar su bloqueo. Conserva literalmente
    los campos de origen y asigna propietario NULL. Un error revierte la
    transacción; no se interpretan bases ausentes o dañadas como éxito.
    """
    origen, destino = Path(ruta_csv), Path(ruta_sqlite)
    if not origen.is_file():
        raise MigracionHistorialError("No se encuentra el historial CSV de origen.")
    if origen.resolve() == destino.resolve():
        raise MigracionHistorialError("El origen y el destino deben ser archivos diferentes.")
    if destino.exists() and os.path.samefile(origen, destino):
        raise MigracionHistorialError("El origen y el destino deben ser archivos diferentes.")
    with bloqueo_historial(origen):
        if not origen.is_file():
            raise MigracionHistorialError("El historial CSV ya no está disponible.")
        try:
            filas = HistorialCSV(origen)._consultar_sin_bloqueo()
        except (OSError, ValueError) as error:
            raise MigracionHistorialError("No se puede leer el historial CSV de origen.") from error
        for fila in filas:
            _validar_fila(fila)
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(str(destino), timeout=5, isolation_level=None)) as conexion:
                conexion.row_factory = sqlite3.Row
                conexion.execute("BEGIN IMMEDIATE")
                try:
                    HistorialSQLite._verificar(conexion, permitir_creacion=True)
                    columnas = ", ".join(CAMPOS)
                    marcadores = ", ".join("?" for _ in CAMPOS)
                    insertadas = existentes = 0
                    for fila in filas:
                        anterior = conexion.execute(
                            f"SELECT {columnas}, usuario_id FROM carreras WHERE identificador=?",
                            (fila["identificador"],),
                        ).fetchone()
                        if anterior is not None:
                            if (anterior["usuario_id"] is not None
                                    or any(anterior[campo] != fila[campo] for campo in CAMPOS)):
                                raise MigracionHistorialError("Una carrera ya existe con propietario o datos diferentes.")
                            existentes += 1
                            continue
                        resultado = conexion.execute(
                            f"INSERT INTO carreras ({columnas}, usuario_id) VALUES ({marcadores}, NULL)",
                            tuple(fila[campo] for campo in CAMPOS),
                        )
                        if resultado.rowcount != 1:
                            raise MigracionHistorialError("No se ha podido insertar una carrera.")
                        insertadas += 1
                    conexion.commit()
                except BaseException:
                    conexion.rollback()
                    raise
        except sqlite3.Error as error:
            raise HistorialSQLiteError("No se ha podido completar la migración a SQLite.") from error
    return ResultadoMigracion(len(filas), insertadas, existentes)