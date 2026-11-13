"""Inspección experimental de dos contenedores; no autoriza cargar credenciales.

Las imágenes esperadas deben proceder de una construcción controlada. El
resultado es una fotografía, no una operación atómica ni una garantía frente al
administrador del equipo o del daemon Docker. No lee secretos ni su almacén.
Los certificados montados y la red pueden cambiar tras la comprobación.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess


class VerificacionProxyError(RuntimeError):
    """La composición no coincide con los parámetros esperados."""


@dataclass(frozen=True)
class EstadoContenedor:
    identificador: str
    imagen: str
    iniciado: str
    reinicios: int


@dataclass(frozen=True)
class EstadoComposicion:
    backend: EstadoContenedor
    proxy: EstadoContenedor


COMANDO_BACKEND = [
    "python", "-m", "taxiroute.servidor_proxy_experimental",
    "--cuentas", "/datos/cuentas.sqlite3", "--historial", "/datos/historial.sqlite3",
    "--tarifas", "/datos/tarifas.json", "--certificado", "/cert/interno.pem",
    "--clave", "/cert/interno.key", "--secreto-gmail", "/run/taxiroute/gmail_oauth.json",
]
_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGEN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROYECTO = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_RUTAS = {"datos", "cert_interno", "clave_interna", "cert_externo", "clave_externa"}


def _docker(args):
    resultado = subprocess.run(["docker", *args], check=True, capture_output=True, timeout=30)
    return resultado.stdout


def _inspeccion(args):
    datos = json.loads(_docker(args))
    if not isinstance(datos, list) or len(datos) != 1 or not isinstance(datos[0], dict):
        raise ValueError("Inspección no válida.")
    return datos[0]


def _ruta(ruta, *, directorio):
    p = Path(ruta).expanduser()
    if p.is_symlink():
        raise ValueError("No se admiten enlaces.")
    p = p.resolve(strict=True)
    if not (p.is_dir() if directorio else p.is_file()):
        raise ValueError("Ruta no válida.")
    return os.path.normcase(str(p))


def _tmpfs_real(actual, esperado):
    if not isinstance(actual, dict) or set(actual) != set(esperado):
        return False
    for destino, opciones in esperado.items():
        valor = actual[destino]
        if not isinstance(valor, str):
            return False
        partes = valor.split(",") if valor else []
        if len(partes) != len(opciones) or set(partes) != set(opciones):
            return False
    return True


def _verificar_montajes(contenedor, esperados):
    montajes = contenedor["Mounts"]
    if not isinstance(montajes, list) or len(montajes) != len(esperados):
        raise ValueError("Montajes no válidos.")
    destinos = [m["Destination"] for m in montajes]
    if len(set(destinos)) != len(destinos) or set(destinos) != set(esperados):
        raise ValueError("Destinos no válidos.")
    for m in montajes:
        ruta, escritura, directorio = esperados[m["Destination"]]
        if (m["Type"] != "bind" or m["RW"] is not escritura
                or _ruta(m["Source"], directorio=directorio) != ruta):
            raise ValueError("Montaje no válido.")


def _comprobar(servicio, contenedor, imagen, esperado, proyecto, montajes):
    identificador = contenedor["Id"]
    estado, config, host = contenedor["State"], contenedor["Config"], contenedor["HostConfig"]
    if (not isinstance(identificador, str) or not _ID.fullmatch(identificador)
            or contenedor["Image"] != esperado or imagen["Id"] != esperado
            or estado["Status"] != "running" or estado["Running"] is not True
            or estado["Paused"] is not False or estado["Restarting"] is not False
            or estado["Dead"] is not False or not isinstance(estado["StartedAt"], str)
            or not estado["StartedAt"].endswith("Z")
            or estado["StartedAt"].startswith("0001-")
            or type(contenedor["RestartCount"]) is not int or contenedor["RestartCount"] < 0):
        raise ValueError("Identidad o estado no válido.")
    etiquetas = config["Labels"]
    if (etiquetas["com.docker.compose.project"] != proyecto
            or etiquetas["com.docker.compose.service"] != servicio
            or etiquetas["com.docker.compose.container-number"] != "1"
            or etiquetas["com.docker.compose.oneoff"] != "False"
            or config["Image"] != f"{proyecto}-{servicio}"
            or config["Env"] != imagen["Config"]["Env"]):
        raise ValueError("Configuración de Compose no válida.")
    if (host["ReadonlyRootfs"] is not True or host["Privileged"] is not False
            or host["CapDrop"] != ["ALL"] or host.get("CapAdd") not in (None, [])
            or host["SecurityOpt"] != ["no-new-privileges:true"]
            or host.get("Devices") not in (None, []) or host.get("DeviceRequests") not in (None, [])
            or host.get("VolumesFrom") not in (None, []) or host.get("GroupAdd") not in (None, [])
            or host.get("PidMode") != "" or host.get("IpcMode") != "private"
            or host.get("UTSMode") != ""):
        raise ValueError("Aislamiento no válido.")
    _verificar_montajes(contenedor, montajes)
    return EstadoContenedor(identificador, esperado, estado["StartedAt"], contenedor["RestartCount"])


def verificar_composicion(*, proyecto, imagenes_esperadas, rutas_esperadas, anterior=None):
    """Devuelve identidad y arranque observados, sin acceso al almacén OAuth.

    Las imágenes deben indicarse por ID inmutable obtenido fuera de esta función,
    a partir de una construcción controlada. Las cinco rutas deben existir.
    Si se facilita el resultado previo, también se rechazan reinicios o cambios.
    No usar esta función como autorización suficiente para transmitir secretos.
    """
    try:
        if (not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto)
                or not isinstance(imagenes_esperadas, dict)
                or set(imagenes_esperadas) != {"backend", "proxy"}
                or not all(isinstance(i, str) and _IMAGEN.fullmatch(i) for i in imagenes_esperadas.values())
                or not isinstance(rutas_esperadas, dict) or set(rutas_esperadas) != _RUTAS
                or (anterior is not None and not isinstance(anterior, EstadoComposicion))):
            raise ValueError("Argumentos no válidos.")
        rutas = {k: _ruta(v, directorio=(k == "datos")) for k, v in rutas_esperadas.items()}
        if len(set(rutas.values())) != len(rutas):
            raise ValueError("Rutas duplicadas.")
        montajes = {
            "backend": {"/datos": (rutas["datos"], True, True),
                        "/cert/interno.pem": (rutas["cert_interno"], False, False),
                        "/cert/interno.key": (rutas["clave_interna"], False, False)},
            "proxy": {"/cert/interno.pem": (rutas["cert_interno"], False, False),
                      "/cert/externo.pem": (rutas["cert_externo"], False, False),
                      "/cert/externo.key": (rutas["clave_externa"], False, False)},
        }
        contenedores, evidencias = {}, {}
        for servicio in ("backend", "proxy"):
            ids = _docker(["ps", "-a", "--no-trunc", "--filter", f"label=com.docker.compose.project={proyecto}",
                           "--filter", f"label=com.docker.compose.service={servicio}", "--format", "{{.ID}}"])
            ids = ids.decode("ascii").splitlines()
            if len(ids) != 1 or not _ID.fullmatch(ids[0]):
                raise ValueError("Se necesita exactamente un contenedor por servicio.")
            c = _inspeccion(["inspect", ids[0]])
            img = _inspeccion(["image", "inspect", imagenes_esperadas[servicio]])
            if c["Id"] != ids[0]:
                raise ValueError("El contenedor cambió.")
            evidencia = _comprobar(servicio, c, img, imagenes_esperadas[servicio], proyecto, montajes[servicio])
            contenedores[servicio], evidencias[servicio] = c, evidencia

        b, p = contenedores["backend"], contenedores["proxy"]
        bc, bh, pc, ph = b["Config"], b["HostConfig"], p["Config"], p["HostConfig"]
        if (bc["User"] != "10001:10001" or bc["WorkingDir"] != "/app"
                or bc["Entrypoint"] not in (None, []) or bc["Cmd"] != COMANDO_BACKEND
                or bh["NetworkMode"] != f"{proyecto}_default"
                or set(b["NetworkSettings"]["Networks"]) != {f"{proyecto}_default"}
                or bh["PortBindings"] != {"18771/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18771"}]}
                or not _tmpfs_real(bh["Tmpfs"], {"/tmp": [], "/run/taxiroute":
                    ["rw", "noexec", "nosuid", "nodev", "uid=10001", "gid=10001", "mode=0700"]})
                or pc["User"] != "101:101" or pc["WorkingDir"] != "/"
                or pc["Entrypoint"] != ["nginx"] or pc["Cmd"] != ["-g", "daemon off;", "-c", "/etc/nginx/nginx.conf"]
                or ph["NetworkMode"] != "container:" + evidencias["backend"].identificador
                or p["NetworkSettings"]["Networks"] != {}
                or ph["PortBindings"] not in ({}, None)
                or not _tmpfs_real(ph["Tmpfs"], {"/tmp":
                    ["rw", "noexec", "nosuid", "nodev", "uid=101", "gid=101", "mode=0700"]})):
            raise ValueError("Servicios incompatibles.")
        actual = EstadoComposicion(evidencias["backend"], evidencias["proxy"])
        if anterior is not None and actual != anterior:
            raise ValueError("Los servicios han cambiado o se han reiniciado.")
        return actual
    except (ValueError, TypeError, KeyError, IndexError, OSError, UnicodeError,
            subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise VerificacionProxyError("No se pudo verificar la composición experimental.") from exc
