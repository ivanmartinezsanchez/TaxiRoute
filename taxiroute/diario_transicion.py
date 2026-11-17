"""Diario conservador de transición LAN; no coordina procesos externos.

Cada registro se sincroniza antes de pasar al siguiente paso. Un diario
truncado o ilegible requiere revisión. Solo es fiable si todos los procesos de
publicación respetan este protocolo y los bloqueos del proyecto.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import uuid

from taxiroute.bloqueo import bloqueo_historial

ARCHIVO_DIARIO = 'diario-transicion-lan.jsonl'
_MAXIMO = 8192
_SIGUIENTES = {
    'PREPARADA': {'PUBLICACION_INTENTADA', 'CANCELACION_SOLICITADA'},
    'PUBLICACION_INTENTADA': {'PUBLICADA'},
    'CANCELACION_SOLICITADA': {'CANCELADA'},
    'PUBLICADA': set(),
    'CANCELADA': set(),
}


class DiarioTransicionError(RuntimeError):
    """El diario no permite continuar sin revisión."""


def _ruta(identidad):
    ruta = Path(identidad).expanduser().absolute()
    if not ruta.is_dir() or any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                                 for p in (ruta, *ruta.parents)):
        raise ValueError('Identidad no válida.')
    return ruta / ARCHIVO_DIARIO


def _contenido(ruta):
    if ruta.is_symlink() or not ruta.is_file():
        raise ValueError('Diario no disponible.')
    with ruta.open('rb') as archivo:
        datos = archivo.read(_MAXIMO + 1)
    if not datos or len(datos) > _MAXIMO or not datos.endswith(b'\n'):
        raise ValueError('Diario incompleto.')
    registros = []
    def sin_repetidos(pares):
        obj = {}
        for clave, valor in pares:
            if clave in obj:
                raise ValueError('Campo repetido.')
            obj[clave] = valor
        return obj
    for linea in datos.splitlines():
        item = json.loads(linea.decode('utf-8'), object_pairs_hook=sin_repetidos)
        if not isinstance(item, dict) or set(item) != {'version', 'id', 'estado'}:
            raise ValueError('Registro no válido.')
        if type(item['version']) is not int or item['version'] != 1:
            raise ValueError('Versión no válida.')
        if not isinstance(item['id'], str) or str(uuid.UUID(item['id'])) != item['id']:
            raise ValueError('Identidad no válida.')
        if item['estado'] not in _SIGUIENTES:
            raise ValueError('Estado no válido.')
        if registros:
            if item['id'] != registros[0]['id'] or item['estado'] not in _SIGUIENTES[registros[-1]['estado']]:
                raise ValueError('Secuencia no válida.')
        elif item['estado'] != 'PREPARADA':
            raise ValueError('Falta el inicio.')
        registros.append(item)
    return registros[-1]


def _agregar(archivo, identificador, estado):
    registro = json.dumps({'version': 1, 'id': identificador, 'estado': estado},
                          sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
    archivo.write(registro)
    archivo.flush()
    os.fsync(archivo.fileno())


def iniciar_diario(identidad):
    """Crea un diario nuevo; si ya existe conserva todo para revisión."""
    try:
        ruta = _ruta(identidad)
        with bloqueo_historial(ruta, espera=0):
            if os.path.lexists(ruta):
                raise ValueError('Ya existe un diario.')
            identificador = str(uuid.uuid4())
            with ruta.open('xb') as archivo:
                _agregar(archivo, identificador, 'PREPARADA')
            return identificador
    except Exception:
        raise DiarioTransicionError('No se pudo crear el diario de transición.') from None


def registrar_estado(identidad, identificador, nuevo):
    """Añade y sincroniza un cambio válido sin sobrescribir el historial."""
    try:
        ruta = _ruta(identidad)
        with bloqueo_historial(ruta, espera=0):
            actual = _contenido(ruta)
            if actual['id'] != identificador or nuevo not in _SIGUIENTES[actual['estado']]:
                raise ValueError('Transición no permitida.')
            with ruta.open('ab') as archivo:
                _agregar(archivo, identificador, nuevo)
            return nuevo
    except Exception:
        raise DiarioTransicionError('No se pudo registrar la transición; requiere revisión.') from None


def consultar_diario(identidad):
    """Devuelve identidad y estado, o rechaza una secuencia incompleta."""
    try:
        ruta = _ruta(identidad)
        with bloqueo_historial(ruta, espera=0):
            return _contenido(ruta)
    except Exception:
        raise DiarioTransicionError('No se pudo leer el diario; requiere revisión.') from None


@contextmanager
def cancelacion_autorizada(identidad, identificador):
    """Mantiene el diario bloqueado mientras se retira una barrera previa."""
    try:
        ruta = _ruta(identidad)
        with bloqueo_historial(ruta, espera=0):
            actual = _contenido(ruta)
            if actual['id'] != identificador or actual['estado'] != 'CANCELACION_SOLICITADA':
                raise ValueError('La cancelación no está autorizada por el diario.')
            yield
    except Exception:
        raise DiarioTransicionError('No se pudo autorizar la cancelación; requiere revisión.') from None
