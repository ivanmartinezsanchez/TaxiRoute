"""Comprobación conservadora de ACL Windows para una instalación experimental.

No modifica ACL ni lee el contenido de los archivos. No garantiza protección
frente a administradores, al servicio Docker, a cambios simultáneos ni a
enlaces duros o mecanismos externos de acceso. Es una fotografía local.
"""
import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess


class PermisosProxyError(RuntimeError):
    """No se ha podido comprobar la privacidad de la instalación."""


_MAX_RUTAS = 512
_MAX_UNIDADES_ENTORNO = 24000
_MAX_SALIDA = 1024 * 1024
_SID = re.compile(r"S-1-(?:\d+-)*\d+\Z")
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$usuario = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$salida = @()
foreach ($ruta in (ConvertFrom-Json -InputObject $env:TAXIROUTE_ACL_PATHS)) {
    $acl = Get-Acl -LiteralPath $ruta
    $dueno = ([Security.Principal.NTAccount]::new($acl.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value
    $reglas = @($acl.Access)
    if ($reglas.Count -gt 64) { throw 'Demasiadas reglas.' }
    $concedidos = @()
    foreach ($regla in $reglas) {
        if ($regla.AccessControlType -eq 'Allow') {
            $concedidos += $regla.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        }
    }
    $salida += @{ propietario = $dueno; permitidos = @($concedidos) }
}
@{ usuario = $usuario; archivos = @($salida) } | ConvertTo-Json -Depth 5 -Compress
'''


def _es_windows():
    return os.name == "nt"


def _sin_redireccion(ruta):
    info = ruta.lstat()
    atributos = getattr(info, "st_file_attributes", 0)
    if (stat.S_ISLNK(info.st_mode) or ruta.is_symlink()
            or (hasattr(ruta, "is_junction") and ruta.is_junction())
            or atributos & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise ValueError("Ruta redirigida.")
    return info


def _rutas(carpeta, permitir_ausente):
    if not isinstance(permitir_ausente, bool):
        raise ValueError("Opción no válida.")
    solicitada = Path(carpeta).expanduser().absolute()
    padre = solicitada.parent
    for componente in (padre, *padre.parents):
        _sin_redireccion(componente)
    if not padre.is_dir():
        raise ValueError("Padre no válido.")
    resultado = [padre]
    if not os.path.lexists(solicitada):
        if not permitir_ausente:
            raise ValueError("Carpeta no disponible.")
        return resultado
    _sin_redireccion(solicitada)
    if not solicitada.is_dir():
        raise ValueError("Carpeta no válida.")
    pendientes = [solicitada]
    while pendientes:
        actual = pendientes.pop()
        info = _sin_redireccion(actual)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("Tipo de archivo no válido.")
        resultado.append(actual)
        if len(resultado) > _MAX_RUTAS:
            raise ValueError("Árbol demasiado grande.")
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(actual) as entradas:
                for entrada in entradas:
                    if len(resultado) + len(pendientes) >= _MAX_RUTAS:
                        raise ValueError("Árbol demasiado grande.")
                    pendientes.append(Path(entrada.path))
    return resultado


def _consultar(rutas):
    directorio_windows = os.environ.get("SystemRoot")
    if not directorio_windows:
        raise ValueError("No se encuentra Windows.")
    sistema = Path(directorio_windows) / "System32" / "WindowsPowerShell" / "v1.0"
    ejecutable = sistema / "powershell.exe"
    serializadas = json.dumps([str(p) for p in rutas], ensure_ascii=False)
    if len(serializadas.encode("utf-16-le")) > _MAX_UNIDADES_ENTORNO * 2:
        raise ValueError("Las rutas exceden el límite.")
    entorno = os.environ.copy()
    entorno["PSModulePath"] = str(sistema / "Modules")
    entorno["TAXIROUTE_ACL_PATHS"] = serializadas
    comando = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    resultado = subprocess.run([str(ejecutable), "-NoProfile", "-NonInteractive", "-EncodedCommand", comando],
        env=entorno, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False)
    if resultado.returncode != 0 or len(resultado.stdout) > _MAX_SALIDA:
        raise ValueError("No se pudieron consultar las ACL.")
    # Windows PowerShell 5.1 emite UTF-8 con BOM en algunos entornos; también
    # se admite su salida ASCII habitual, que es un subconjunto de UTF-8.
    return json.loads(resultado.stdout.decode("utf-8-sig"))


def verificar_permisos_windows(carpeta, *, permitir_ausente=False):
    """Exige propietario actual y concesiones solo a usuario, sistema y admins.

    Para una carpeta aún no creada, permitir_ausente comprueba solamente su
    padre. OWNER RIGHTS se admite exclusivamente tras verificar el propietario.
    No es una garantía de seguridad absoluta ni una reserva de permisos.
    """
    try:
        if not _es_windows():
            raise ValueError("La comprobación requiere Windows.")
        rutas = _rutas(carpeta, permitir_ausente)
        datos = _consultar(rutas)
        if not isinstance(datos, dict) or set(datos) != {"usuario", "archivos"}:
            raise ValueError("Respuesta no válida.")
        usuario = datos["usuario"]
        archivos = datos["archivos"]
        if (not isinstance(usuario, str) or not _SID.fullmatch(usuario)
                or usuario in {"S-1-3-4", "S-1-5-18", "S-1-5-32-544"}
                or not isinstance(archivos, list) or len(archivos) != len(rutas)):
            raise ValueError("Identidad no válida.")
        permitidos = {usuario, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
        for item in archivos:
            if (not isinstance(item, dict) or set(item) != {"propietario", "permitidos"}
                    or item["propietario"] != usuario or not isinstance(item["permitidos"], list)
                    or not item["permitidos"] or len(item["permitidos"]) > 64
                    or not all(isinstance(sid, str) and sid in permitidos for sid in item["permitidos"])):
                raise ValueError("ACL no válida.")
    except (OSError, TypeError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
        raise PermisosProxyError("No se han podido comprobar los permisos de la instalación.") from None
