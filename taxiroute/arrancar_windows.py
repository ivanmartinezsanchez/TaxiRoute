"""Arranque de una instalación Compose local ya preparada; experimental."""
import argparse
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
import webbrowser

from taxiroute.cargar_gmail_windows import (
    PROYECTO, ErrorCarga, _docker, _estado_archivo, _verificar_contenedor, cargar,
)

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = RAIZ / "compose.sqlite-temporal-experimental.yaml"
URL = "https://127.0.0.1:18770/"


class ErrorArranque(RuntimeError):
    """No se pudo comprobar o iniciar la instalación experimental."""


def _ruta_existente(valor, *, directorio):
    if not valor:
        raise ErrorArranque("Falta indicar una de las rutas necesarias.")
    ruta = Path(valor).expanduser()
    if ruta.is_symlink():
        raise ErrorArranque("No se admiten enlaces como rutas de instalación.")
    ruta = ruta.resolve(strict=True)
    if not (ruta.is_dir() if directorio else ruta.is_file()):
        raise ErrorArranque("Una de las rutas indicadas no es válida.")
    return ruta


def _esperar_https(certificado):
    contexto = ssl.create_default_context()
    # Confiar en el certificado elegido para esta comprobación no lo instala en el navegador.
    contexto.load_verify_locations(cafile=str(certificado))
    cliente = build_opener(ProxyHandler({}), HTTPSHandler(context=contexto))
    limite = time.monotonic() + 30
    while True:
        try:
            with cliente.open(Request(URL + "acceso"), timeout=3) as respuesta:
                if respuesta.status == 200:
                    return
        except (OSError, ssl.SSLError):
            pass
        if time.monotonic() >= limite:
            raise ErrorArranque("El servicio no respondió con el certificado indicado.") from None
        time.sleep(0.5)


def arrancar(datos, certificado, clave, *, abrir=True):
    """No detiene, recrea ni sustituye una instalación existente."""
    try:
        datos = _ruta_existente(datos, directorio=True)
        certificado = _ruta_existente(certificado, directorio=False)
        clave = _ruta_existente(clave, directorio=False)
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            archivo = datos / nombre
            if archivo.is_symlink() or not archivo.is_file():
                raise ErrorArranque("La instalación no contiene las bases y tarifas esperadas.")
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(certificado, clave)
        rutas = {"/datos": datos, "/run/secrets/tls_certificado": certificado,
                 "/run/secrets/tls_clave": clave}
        ids = _docker(["ps", "-a", "--no-trunc", "--filter", f"label=com.docker.compose.project={PROYECTO}",
                       "--filter", "label=com.docker.compose.service=taxiroute", "--format", "{{.ID}}"]).decode("ascii").splitlines()
        if len(ids) > 1:
            raise ErrorArranque("Hay más de un contenedor para esta instalación; revísalos antes de continuar.")
        if ids:
            identificador = _verificar_contenedor(permitir_archivo=True, permitir_detenido=True, rutas_esperadas=rutas)
            inspeccion = json.loads(_docker(["inspect", identificador]))[0]
            if not inspeccion["State"]["Running"]:
                _docker(["start", identificador])
        else:
            entorno = os.environ.copy()
            entorno.update(TAXIROUTE_DATOS=str(datos), TAXIROUTE_CERTIFICADO=str(certificado), TAXIROUTE_CLAVE_TLS=str(clave))
            resultado = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "up", "--build", "--no-recreate", "-d"],
                cwd=RAIZ, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
            if resultado.returncode:
                raise ErrorArranque("Docker no pudo iniciar la instalación.")
        _esperar_https(certificado)
        identificador = _verificar_contenedor(permitir_archivo=True, rutas_esperadas=rutas)
        estado = _estado_archivo(identificador)
        if estado == "ausente":
            cargar(identificador_esperado=identificador, rutas_esperadas=rutas)
            mensaje = "Autorización de Gmail cargada."
        else:
            mensaje = "Ya había una autorización temporal; no se ha comprobado a qué cuenta pertenece ni su vigencia."
        if abrir:
            abierto = webbrowser.open(URL)
            if not abierto:
                mensaje += " No se pudo abrir el navegador automáticamente."
        return mensaje
    except ErrorArranque:
        raise
    except (ErrorCarga, OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise ErrorArranque("No se pudo verificar o iniciar TaxiRoute. El contenedor no se ha detenido automáticamente.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arranca una instalación SQLite local experimental ya preparada.")
    parser.add_argument("--datos", default=os.environ.get("TAXIROUTE_DATOS"), help="Directorio de datos existente")
    parser.add_argument("--certificado", default=os.environ.get("TAXIROUTE_CERTIFICADO"), help="Certificado TLS existente")
    parser.add_argument("--clave", default=os.environ.get("TAXIROUTE_CLAVE_TLS"), help="Clave TLS existente")
    args = parser.parse_args(argv)
    try:
        print("Comprobando Docker y la instalación local…", flush=True)
        mensaje = arrancar(args.datos, args.certificado, args.clave)
    except ErrorArranque as error:
        print(str(error), file=sys.stderr)
        return 1
    print(mensaje)
    print(f"TaxiRoute: {URL}")
    print("Instalación experimental: solo datos de prueba. El navegador puede advertir sobre un certificado autofirmado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
