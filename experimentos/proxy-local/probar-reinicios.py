"""Ensayo Docker aislado de reinicios; solo datos ficticios.

Requiere Docker Desktop, dependencias del proyecto y localhost:18771 libre.
No reinicia Docker Desktop ni toca proyectos ajenos. El resultado observado
cuando solo se reinicia el backend depende del motor Docker.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import ssl
import subprocess
import sys
import time
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
import uuid

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite

spec = spec_from_file_location("ensayo_proxy", Path(__file__).with_name("probar-integracion.py"))
util = module_from_spec(spec)
spec.loader.exec_module(util)


def ejecutar(argumentos, *, entorno=None, comprobar=True):
    return subprocess.run(argumentos, cwd=RAIZ, env=entorno, check=comprobar,
                          capture_output=True, text=True, timeout=180)


def estado(identificador):
    info = json.loads(ejecutar(["docker", "inspect", identificador]).stdout)[0]
    return {
        "id": info["Id"],
        "activo": info["State"]["Running"],
        "inicio": info["State"]["StartedAt"],
        "red": info["HostConfig"]["NetworkMode"],
    }


def respuesta_https(certificado):
    contexto = ssl.create_default_context(cafile=str(certificado))
    cliente = build_opener(ProxyHandler({}), HTTPSHandler(context=contexto))
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        try:
            with cliente.open("https://127.0.0.1:18771/acceso", timeout=2) as respuesta:
                return respuesta.status
        except (OSError, ssl.SSLError):
            time.sleep(0.5)
    return None


def proyecto_nuevo(nombre):
    filtro = "label=com.docker.compose.project=" + nombre
    consultas = [
        ["docker", "ps", "-a", "--filter", filtro, "--format", "{{.ID}}"],
        ["docker", "network", "ls", "--filter", filtro, "--format", "{{.ID}}"],
        ["docker", "volume", "ls", "--filter", filtro, "--format", "{{.Name}}"],
    ]
    if any(ejecutar(c).stdout.strip() for c in consultas):
        return False
    for servicio in ("backend", "proxy"):
        if ejecutar(["docker", "image", "inspect", f"{nombre}-{servicio}"],
                    comprobar=False).returncode == 0:
            return False
    return True


def main():
    nombre = "taxiroute-reinicios-" + uuid.uuid4().hex
    if not proyecto_nuevo(nombre):
        raise RuntimeError("El nombre temporal ya tiene recursos; no se limpiarán.")
    compose = ["docker", "compose", "-p", nombre, "-f", str(util.COMPOSE)]
    with TemporaryDirectory(prefix="taxiroute-reinicios-ficticio-") as temporal:
        base = Path(temporal)
        datos = preparar_demo_sqlite(base / "datos")
        interno, llave_i = util.certificado(base, "interno", dns="taxiroute-backend.local")
        externo, llave_e = util.certificado(base, "externo", dns=None)
        entorno = {**os.environ, "TAXIROUTE_PROXY_DATOS": str(datos),
                   "TAXIROUTE_CERT_INTERNO": str(interno), "TAXIROUTE_CLAVE_INTERNA": str(llave_i),
                   "TAXIROUTE_CERT_EXTERNO": str(externo), "TAXIROUTE_CLAVE_EXTERNA": str(llave_e)}
        try:
            ejecutar(compose + ["up", "--build", "-d"], entorno=entorno)
            backend = ejecutar(compose + ["ps", "-q", "backend"], entorno=entorno).stdout.strip()
            proxy = ejecutar(compose + ["ps", "-q", "proxy"], entorno=entorno).stdout.strip()
            if not backend or not proxy or respuesta_https(externo) != 200:
                raise RuntimeError("No se inició el servicio HTTPS ficticio.")
            inicial_backend, inicial_proxy = estado(backend), estado(proxy)
            if inicial_proxy["red"] != "container:" + backend:
                raise RuntimeError("El proxy no comparte la red del backend de prueba.")
            print("Inicio con HTTPS: 200.", flush=True)

            ejecutar(["docker", "exec", "--user", "10001:10001", backend, "python", "-c",
                      "from pathlib import Path; Path('/run/taxiroute/marcador-ficticio').write_text('ficticio')"])
            ejecutar(["docker", "restart", "--time", "3", proxy])
            tras_proxy, backend_sin_reinicio = estado(proxy), estado(backend)
            if (respuesta_https(externo) != 200 or tras_proxy["id"] != inicial_proxy["id"]
                    or tras_proxy["inicio"] == inicial_proxy["inicio"]
                    or backend_sin_reinicio["inicio"] != inicial_backend["inicio"]):
                raise RuntimeError("El reinicio aislado del proxy no respondió como se esperaba.")
            print("Reinicio del proxy: HTTPS 200 y backend sin reiniciar.", flush=True)

            ejecutar(["docker", "restart", "--time", "3", backend])
            tras_backend = estado(backend)
            if tras_backend["id"] != inicial_backend["id"] or tras_backend["inicio"] == inicial_backend["inicio"]:
                raise RuntimeError("No se pudo verificar el reinicio del backend.")
            archivo = ejecutar(["docker", "exec", "--user", "10001:10001", backend, "test", "-e",
                                "/run/taxiroute/marcador-ficticio"], comprobar=False)
            if archivo.returncode != 1:
                raise RuntimeError("No se pudo confirmar el vaciado del archivo ficticio de tmpfs.")
            observacion = respuesta_https(externo)
            print(f"Tras reiniciar solo backend: HTTPS {observacion if observacion is not None else 'sin respuesta'}; tmpfs vacío.", flush=True)

            ejecutar(["docker", "restart", "--time", "3", proxy])
            final_backend, final_proxy = estado(backend), estado(proxy)
            if (respuesta_https(externo) != 200 or final_backend["inicio"] != tras_backend["inicio"]
                    or final_proxy["id"] != inicial_proxy["id"] or final_proxy["red"] != "container:" + backend):
                raise RuntimeError("No se recuperó HTTPS al reiniciar únicamente el proxy.")
            print("Reinicio posterior del proxy: HTTPS 200 sin reiniciar otra vez el backend.", flush=True)
        finally:
            ejecutar(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"], entorno=entorno)


if __name__ == "__main__":
    main()
