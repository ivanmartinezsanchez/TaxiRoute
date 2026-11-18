"""Carga explícita del perfil alternativo en el Compose local experimental.

No crea archivos en Windows ni muestra credenciales. Requiere Docker Desktop.
"""
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys

from taxiroute.oauth_gmail import (
    CLAVE_ALMACEN_ALTERNATIVA, SERVICIO_ALMACEN, _almacen_windows,
)

PROYECTO = "taxiroute-temporal-experimental"
SERVICIO = "taxiroute"
IMAGEN = "taxiroute-temporal-experimental-taxiroute"
CARGADOR = ["/usr/local/bin/python", "-I", "-c",
    "import sys,runpy; sys.path.insert(0,'/app'); runpy.run_module('taxiroute.cargar_oauth_temporal',run_name='__main__')"]
COMANDO_SERVIDOR = ["python", "-m", "taxiroute.iniciar_sqlite_local", "--cuentas", "/datos/cuentas.sqlite3",
    "--historial", "/datos/historial.sqlite3", "--tarifas", "/datos/tarifas.json", "--certificado",
    "/run/secrets/tls_certificado", "--clave", "/run/secrets/tls_clave", "--secreto-gmail",
    "/run/taxiroute/gmail_oauth.json", "--puerto", "18770", "--direccion", "127.0.0.1", "--escucha-contenedor"]


class ErrorCarga(RuntimeError):
    """No se ha podido verificar el destino o cargar la autorización."""


def _docker(argumentos, *, entrada=None):
    try:
        resultado = subprocess.run(["docker", *argumentos], input=entrada,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        if resultado.returncode != 0:
            raise ValueError
        return resultado.stdout
    except Exception:
        raise ErrorCarga("No se ha podido verificar o cargar la autorización en Docker.") from None


def _verificar_contenedor(*, permitir_archivo=False, permitir_detenido=False, rutas_esperadas=None):
    """Inspecciona el destino antes de acceder al almacén de Windows.

    Las etiquetas y la imagen local no protegen frente al administrador del
    equipo o del daemon Docker. El ID evita seleccionar otro contenedor por nombre.
    """
    try:
        salida = _docker(["ps", *( ["-a"] if permitir_detenido else [] ), "--no-trunc", "--filter", f"label=com.docker.compose.project={PROYECTO}",
            "--filter", f"label=com.docker.compose.service={SERVICIO}", "--format", "{{.ID}}"])
        identificadores = salida.decode("ascii").splitlines()
        if len(identificadores) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identificadores[0]):
            raise ValueError
        identificador = identificadores[0]
        inspeccion = json.loads(_docker(["inspect", identificador]))
        if not isinstance(inspeccion, list) or len(inspeccion) != 1:
            raise ValueError
        contenedor = inspeccion[0]
        config, host = contenedor["Config"], contenedor["HostConfig"]
        etiquetas = config["Labels"]
        if (contenedor["Id"] != identificador or (not permitir_detenido and not contenedor["State"]["Running"])
                or etiquetas.get("com.docker.compose.project") != PROYECTO
                or etiquetas.get("com.docker.compose.service") != SERVICIO
                or config["Image"] != IMAGEN or config["User"] != "10001:10001"
                or config.get("WorkingDir") != "/app" or config.get("Cmd") != COMANDO_SERVIDOR
                or not host["ReadonlyRootfs"] or host["Privileged"]
                or "ALL" not in (host.get("CapDrop") or [])
                or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])):
            raise ValueError
        montaje = host.get("Tmpfs", {}).get("/run/taxiroute", "")
        opciones = montaje.split(",")
        if len(opciones) != 7 or set(opciones) != {"rw", "noexec", "nosuid", "nodev", "uid=10001", "gid=10001", "mode=0700"}:
            raise ValueError
        if host.get("PortBindings") != {"18770/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18770"}]}:
            raise ValueError
        permitidos = {"/datos", "/run/secrets/tls_certificado", "/run/secrets/tls_clave"}
        if any(str(PurePosixPath(m["Destination"])) not in permitidos for m in contenedor.get("Mounts", [])):
            raise ValueError
        imagen = json.loads(_docker(["image", "inspect", IMAGEN]))
        if (len(imagen) != 1 or imagen[0]["Id"] != contenedor["Image"]
                or imagen[0]["Config"].get("Entrypoint") not in (None, [])
                or config.get("Entrypoint") not in (None, [])
                or config.get("Env") != imagen[0]["Config"].get("Env")):
            raise ValueError
        if rutas_esperadas is not None:
            from pathlib import Path
            import os
            montajes = {m["Destination"]: m for m in contenedor.get("Mounts", [])}
            if set(montajes) != permitidos:
                raise ValueError
            for destino, ruta in rutas_esperadas.items():
                if os.path.normcase(str(Path(montajes[destino]["Source"]).resolve())) != os.path.normcase(str(Path(ruta).resolve())):
                    raise ValueError
                if montajes[destino]["RW"] != (destino == "/datos"):
                    raise ValueError
        if contenedor["State"]["Running"]:
            estado = _estado_archivo(identificador)
            if estado == "presente" and not permitir_archivo:
                raise ValueError
        return identificador
    except Exception:
        raise ErrorCarga("No se ha podido verificar el contenedor de TaxiRoute.") from None


def _estado_archivo(identificador):
    """Comprueba el directorio y, si existe, el archivo sin leer su contenido."""
    prueba = ("import os,stat,sys; from pathlib import Path; p=Path('/run/taxiroute'); "
        "s=p.lstat(); ok=stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid()==10001 "
        "and stat.S_IMODE(s.st_mode)==0o700; f=p/'gmail_oauth.json'; "
        "s=f.lstat() if os.path.lexists(f) else None; "
        "ok=ok and (s is None or (stat.S_ISREG(s.st_mode) and s.st_uid==10001 "
        "and stat.S_IMODE(s.st_mode)==0o600)); "
        "print('presente' if s else 'ausente') if ok else sys.exit(1)")
    salida = _docker(["exec", "--user", "10001:10001", "--workdir", "/app", identificador,
                     "/usr/local/bin/python", "-I", "-c", prueba]).decode("ascii").strip()
    if salida not in ("presente", "ausente"):
        raise ErrorCarga("No se ha podido verificar la autorización temporal.")
    return salida


def cargar(*, identificador_esperado=None, rutas_esperadas=None):
    """Comprueba el destino y luego envía la autorización por stdin, una vez."""
    try:
        identificador = _verificar_contenedor(rutas_esperadas=rutas_esperadas)
        if identificador_esperado is not None and identificador != identificador_esperado:
            raise ValueError
        contenido = _almacen_windows().get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN_ALTERNATIVA)
        if not isinstance(contenido, str) or not contenido:
            raise ValueError
        datos = contenido.encode("utf-8")
        if len(datos) > 32768:
            raise ValueError
        _docker(["exec", "-i", "--user", "10001:10001", "--workdir", "/app", identificador, *CARGADOR], entrada=datos)
        # Un reinicio puede vaciar tmpfs mientras se está realizando la carga.
        comprobar = ("import os,stat,sys; from pathlib import Path; "
            "s=Path('/run/taxiroute/gmail_oauth.json').lstat(); "
            "ok=stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()==10001 "
            "and stat.S_IMODE(s.st_mode)==0o600; sys.exit(0 if ok else 1)")
        _docker(["exec", "--user", "10001:10001", "--workdir", "/app", identificador,
                 "/usr/local/bin/python", "-I", "-c", comprobar])
    except Exception:
        raise ErrorCarga("No se ha podido cargar la autorización temporal.") from None


def main():
    try:
        cargar()
    except ErrorCarga:
        print("No se ha podido cargar la autorización temporal. Revisa el contenedor y el almacén de Windows.", file=sys.stderr)
        return 1
    print("Autorización temporal cargada en el contenedor local de TaxiRoute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
