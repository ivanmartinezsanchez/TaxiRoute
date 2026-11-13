"""Primer arranque local aislado del proxy, solo con instalaciones ficticias.

No gestiona Gmail ni adopta contenedores existentes. Las identidades solo
viven en memoria: la repetición y el reinicio requieren otro procedimiento.
El control de concurrencia entre la inspección y Compose no es atómico.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from taxiroute.arrancar_windows import _ruta_existente
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.verificador_proxy_experimental import EstadoComposicion, verificar_composicion

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = RAIZ / "compose.proxy-local-experimental.yaml"
PROYECTO = "taxiroute-proxy-local-experimental"
URL = "https://127.0.0.1:18771/acceso"
_ID_IMAGEN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROYECTO = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")


class ErrorArranqueProxy(RuntimeError):
    """El arranque experimental requiere revisión."""


@dataclass(frozen=True)
class ResultadoArranque:
    evidencia: EstadoComposicion
    imagen_backend: str
    imagen_proxy: str


def _validar_certificados(certificado, clave, *, interno):
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(str(certificado), str(clave))
    cert = x509.load_pem_x509_certificate(certificado.read_bytes())
    ahora = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= ahora <= cert.not_valid_after_utc:
        raise ValueError("Certificado fuera de vigencia.")
    nombres = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if ip_address("127.0.0.1") not in nombres.get_values_for_type(x509.IPAddress):
        raise ValueError("Falta la dirección local.")
    if interno and "taxiroute-backend.local" not in nombres.get_values_for_type(x509.DNSName):
        raise ValueError("Falta el nombre interno.")


def _claves_distintas(interno, externo):
    claves = []
    for ruta in (interno, externo):
        cert = x509.load_pem_x509_certificate(ruta.read_bytes())
        claves.append(cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
    if claves[0] == claves[1]:
        raise ValueError("Los certificados deben usar claves diferentes.")


def _sin_contenedores(proyecto):
    salida = _docker(["ps", "-a", "--no-trunc", "--filter",
                     f"label=com.docker.compose.project={proyecto}", "--format", "{{.ID}}"])
    if salida.strip():
        raise ErrorArranqueProxy("Ya hay contenedores del proyecto; se requiere revisión.")


def _componer(argumentos, *, proyecto, entorno):
    salida = subprocess.run(["docker", "compose", "-p", proyecto, "-f", str(COMPOSE), *argumentos],
                           cwd=RAIZ, env=entorno, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=240, check=False)
    if salida.returncode:
        raise ErrorArranqueProxy("Docker no pudo preparar la instalación experimental.")


def _esperar_https(certificado):
    contexto = ssl.create_default_context(cafile=str(certificado))
    cliente = build_opener(ProxyHandler({}), HTTPSHandler(context=contexto))
    limite = time.monotonic() + 30
    while True:
        try:
            with cliente.open(Request(URL), timeout=3) as respuesta:
                if respuesta.status == 200:
                    return
        except (OSError, ssl.SSLError):
            pass
        if time.monotonic() >= limite:
            raise ErrorArranqueProxy("No respondió HTTPS con el certificado indicado.") from None
        time.sleep(0.5)


def arrancar_proxy(*, datos, cert_interno, clave_interna, cert_externo, clave_externa,
                   proyecto=PROYECTO):
    """Solo primer arranque con datos ficticios; nunca sustituye contenedores.

    Construye imágenes y captura IDs antes de iniciar. No lee ni envía OAuth,
    no conserva los IDs tras salir y no configura confianza en el navegador.
    """
    try:
        if not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto):
            raise ValueError
        rutas = {k: _ruta_existente(v, directorio=(k == "datos")) for k, v in {
            "datos": datos, "cert_interno": cert_interno, "clave_interna": clave_interna,
            "cert_externo": cert_externo, "clave_externa": clave_externa}.items()}
        if len(set(rutas.values())) != 5:
            raise ValueError
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            archivo = rutas["datos"] / nombre
            if archivo.is_symlink() or not archivo.is_file():
                raise ValueError
        _validar_certificados(rutas["cert_interno"], rutas["clave_interna"], interno=True)
        _validar_certificados(rutas["cert_externo"], rutas["clave_externa"], interno=False)
        _claves_distintas(rutas["cert_interno"], rutas["cert_externo"])
        entorno = os.environ.copy()
        entorno.update(TAXIROUTE_PROXY_DATOS=str(rutas["datos"]),
                       TAXIROUTE_CERT_INTERNO=str(rutas["cert_interno"]),
                       TAXIROUTE_CLAVE_INTERNA=str(rutas["clave_interna"]),
                       TAXIROUTE_CERT_EXTERNO=str(rutas["cert_externo"]),
                       TAXIROUTE_CLAVE_EXTERNA=str(rutas["clave_externa"]))
        _sin_contenedores(proyecto)
        _componer(["build"], proyecto=proyecto, entorno=entorno)
        imagenes = {}
        for servicio in ("backend", "proxy"):
            valor = _docker(["image", "inspect", f"{proyecto}-{servicio}", "--format", "{{.Id}}"]).decode("ascii").strip()
            if not _ID_IMAGEN.fullmatch(valor):
                raise ValueError
            imagenes[servicio] = valor
        if imagenes["backend"] == imagenes["proxy"]:
            raise ValueError
        _sin_contenedores(proyecto)
        _componer(["up", "-d", "--no-build", "--no-recreate"], proyecto=proyecto, entorno=entorno)
        evidencia = verificar_composicion(proyecto=proyecto, imagenes_esperadas=imagenes,
                                         rutas_esperadas=rutas)
        _esperar_https(rutas["cert_externo"])
        verificar_composicion(proyecto=proyecto, imagenes_esperadas=imagenes,
                              rutas_esperadas=rutas, anterior=evidencia)
        return ResultadoArranque(evidencia, imagenes["backend"], imagenes["proxy"])
    except ErrorArranqueProxy:
        raise
    except Exception:
        raise ErrorArranqueProxy("No se pudo verificar el arranque. Los contenedores no se han detenido automáticamente.") from None
