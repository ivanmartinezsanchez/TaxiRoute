"""Manifiesto experimental: detecta cambios accidentales; no acredita origen.

No guarda claves ni tokens. No es una protección frente a administradores ni
frente a una copia antigua. Los permisos efectivos de Windows deben verificarse
antes de usar datos reales.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ARCHIVO = "manifiesto-proxy.json"
MARCADOR = "manifiesto-proxy.completo"
_MAXIMO = 16384
_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGEN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROYECTO = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_RUTAS = {"datos", "cert_interno", "clave_interna", "cert_externo", "clave_externa"}


class ErrorManifiesto(ValueError):
    """La identidad de la instalación no se ha podido confirmar."""


@dataclass(frozen=True)
class Manifiesto:
    version: int
    instalacion: str
    proyecto: str
    imagenes: dict
    contenedores: dict
    rutas: dict
    certificados: dict


def _ruta(valor, *, directorio):
    p = Path(valor).expanduser().absolute()
    for componente in (p, *p.parents):
        if componente.is_symlink() or (hasattr(componente, "is_junction") and componente.is_junction()):
            raise ValueError("No se admiten enlaces o uniones.")
    p = p.resolve(strict=True)
    if not (p.is_dir() if directorio else p.is_file()):
        raise ValueError("Ruta no válida.")
    return os.path.normcase(str(p))


def _datos(proyecto, imagenes, contenedores, rutas, *, instalacion=None):
    if (not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto)
            or not isinstance(imagenes, dict) or set(imagenes) != {"backend", "proxy"}
            or not isinstance(contenedores, dict) or set(contenedores) != {"backend", "proxy"}
            or not all(isinstance(x, str) and _IMAGEN.fullmatch(x) for x in imagenes.values())
            or not all(isinstance(x, str) and _ID.fullmatch(x) for x in contenedores.values())
            or len(set(imagenes.values())) != 2 or len(set(contenedores.values())) != 2
            or not isinstance(rutas, dict) or set(rutas) != _RUTAS):
        raise ValueError("Datos no válidos.")
    caminos = {k: _ruta(v, directorio=k == "datos") for k, v in rutas.items()}
    if len(set(caminos.values())) != len(caminos):
        raise ValueError("Rutas repetidas.")
    ident = str(uuid.uuid4()) if instalacion is None else instalacion
    if not isinstance(ident, str) or str(uuid.UUID(ident)) != ident:
        raise ValueError("Identificador no válido.")
    hashes = {k: hashlib.sha256(Path(caminos[k]).read_bytes()).hexdigest()
              for k in ("cert_interno", "cert_externo")}
    return Manifiesto(1, ident, proyecto, dict(imagenes), dict(contenedores), caminos, hashes)


def crear_manifiesto(carpeta, *, proyecto, imagenes, contenedores, rutas):
    """Crea un archivo nuevo; conserva cualquier escritura parcial para revisión."""
    try:
        carpeta = Path(_ruta(carpeta, directorio=True))
        if any(os.path.lexists(p / ".git") for p in (carpeta, *carpeta.parents)):
            raise ValueError("El manifiesto debe estar fuera de Git.")
        manifiesto = _datos(proyecto, imagenes, contenedores, rutas)
        contenido = json.dumps(asdict(manifiesto), ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(contenido) > _MAXIMO:
            raise ValueError("Manifiesto demasiado grande.")
        if os.path.lexists(carpeta / MARCADOR):
            raise ValueError("Existe un marcador anterior.")
        with (carpeta / ARCHIVO).open("xb") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        with (carpeta / MARCADOR).open("xb") as archivo:
            archivo.write(hashlib.sha256(contenido).hexdigest().encode("ascii"))
            archivo.flush()
            os.fsync(archivo.fileno())
        return manifiesto
    except (OSError, TypeError, ValueError) as error:
        raise ErrorManifiesto("No se pudo crear el manifiesto; no se sobrescribió ningún archivo.") from error


def leer_manifiesto(carpeta):
    """Rechaza archivos incompletos, rutas cambiadas o certificados distintos."""
    try:
        carpeta = Path(_ruta(carpeta, directorio=True))
        if any(os.path.lexists(p / ".git") for p in (carpeta, *carpeta.parents)):
            raise ValueError("El manifiesto debe estar fuera de Git.")
        archivo = carpeta / ARCHIVO
        marcador = carpeta / MARCADOR
        if (archivo.is_symlink() or not archivo.is_file()
                or marcador.is_symlink() or not marcador.is_file()):
            raise ValueError("Archivo no disponible.")
        with archivo.open("rb") as entrada:
            contenido = entrada.read(_MAXIMO + 1)
        if len(contenido) > _MAXIMO:
            raise ValueError("Archivo demasiado grande.")
        with marcador.open("rb") as entrada:
            huella = entrada.read(65)
        if huella != hashlib.sha256(contenido).hexdigest().encode("ascii"):
            raise ValueError("Manifiesto incompleto o alterado.")
        def sin_duplicados(pares):
            resultado = {}
            for clave, valor in pares:
                if clave in resultado:
                    raise ValueError("Campos duplicados.")
                resultado[clave] = valor
            return resultado
        valor = json.loads(contenido.decode("utf-8"), object_pairs_hook=sin_duplicados)
        if (not isinstance(valor, dict) or set(valor) != set(Manifiesto.__dataclass_fields__)
                or type(valor["version"]) is not int or valor["version"] != 1):
            raise ValueError("Formato no válido.")
        actual = _datos(valor["proyecto"], valor["imagenes"], valor["contenedores"], valor["rutas"],
                        instalacion=valor["instalacion"])
        if asdict(actual) != valor:
            raise ValueError("La instalación ha cambiado.")
        return actual
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as error:
        raise ErrorManifiesto("No se pudo comprobar el manifiesto; es necesaria una revisión.") from error
