"""Lectura y validación de tarifas para iniciar una carrera."""

from decimal import Decimal, InvalidOperation
import json
import os
import tempfile
from pathlib import Path
import re

from taxiroute.carrera import EstadoVehiculo


class ConfiguracionError(ValueError):
    """Las tarifas no se han podido leer o validar."""


def _sin_claves_repetidas(pares):
    datos = {}
    for clave, valor in pares:
        if clave in datos:
            raise ConfiguracionError(f"La clave '{clave}' está repetida")
        datos[clave] = valor
    return datos


def cargar_tarifas(ruta):
    """Devuelve las tarifas decimales, o informa de un error sin usar valores anteriores."""
    ruta = Path(ruta)
    try:
        with ruta.open('r', encoding='utf-8') as archivo:
            datos = json.load(archivo, object_pairs_hook=_sin_claves_repetidas)
    except FileNotFoundError as error:
        raise ConfiguracionError(f"No se encuentra el archivo de tarifas: {ruta}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfiguracionError(f"No se puede leer el archivo de tarifas: {error}") from error

    return validar_tarifas(datos)


def validar_tarifas(datos):
    """La lectura y la edición utilizan exactamente las mismas reglas."""
    campos = {'parado': EstadoVehiculo.PARADO, 'en_movimiento': EstadoVehiculo.EN_MOVIMIENTO}
    if not isinstance(datos, dict) or set(datos) != set(campos):
        raise ConfiguracionError("El archivo debe contener solo 'parado' y 'en_movimiento'")
    tarifas = {}
    for nombre, estado in campos.items():
        valor = datos[nombre]
        if not isinstance(valor, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', valor):
            raise ConfiguracionError(f"La tarifa '{nombre}' debe ser un decimal no negativo entre comillas, por ejemplo '0.02'")
        parte_entera, _, parte_decimal = valor.partition('.')
        if len(parte_entera) > 12 or len(parte_decimal) > 9:
            raise ConfiguracionError(
                f"La tarifa '{nombre}' admite como máximo 12 cifras enteras y 9 decimales"
            )
        try:
            tarifas[estado] = Decimal(valor)
        except InvalidOperation as error:
            raise ConfiguracionError(f"La tarifa '{nombre}' no es válida") from error
    return tarifas


def guardar_tarifas(ruta, datos):
    """Valida antes de escribir y sustituye el JSON completo, sin escritura parcial."""
    validar_tarifas(datos)
    ruta = Path(ruta)
    descriptor, nombre = tempfile.mkstemp(prefix=".tarifas-", suffix=".tmp", dir=ruta.parent)
    temporal = Path(nombre)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.write("\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
    finally:
        temporal.unlink(missing_ok=True)
