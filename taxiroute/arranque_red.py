"""Primer arranque explícito de una composición LAN independiente.

No migra instalaciones ni carga credenciales. La red y la confianza TLS de
otros dispositivos deben configurarse y comprobarse por separado.
"""
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from taxiroute.arranque_proxy_experimental import (
    _claves_distintas, _sin_contenedores, _validar_certificados,
)
from taxiroute.arrancar_windows import _ruta_existente
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.copia_sqlite_experimental import _validar
from taxiroute.certificados_lan import verificar_certificado_externo
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.manifiesto_proxy_experimental import ARCHIVO, MARCADOR, crear_manifiesto_red
from taxiroute.reutilizacion_proxy_experimental import _bloqueo_instalacion, _exigir_sin_actualizacion_pendiente
from taxiroute.red_local import comprobar_direccion_local
from taxiroute.verificador_proxy_experimental import verificar_composicion_red, verificar_instalacion_red

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = RAIZ / "compose.proxy-red-experimental.yaml"
_PROYECTO = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_IMAGEN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ErrorArranqueRed(RuntimeError):
    """El arranque LAN requiere revisión."""


def _compose(argumentos, proyecto, entorno):
    resultado = subprocess.run(["docker", "compose", "-p", proyecto, "-f", str(COMPOSE), *argumentos],
        cwd=RAIZ, env=entorno, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
    if resultado.returncode:
        raise ErrorArranqueRed("Docker no pudo preparar la composición de red.")


def _esperar_https(certificado, direccion, puerto):
    # La autoridad PEM debe corresponder a la cadena previamente verificada.
    # El modo legado autofirmado mantiene su comprobación original.
    from cryptography import x509
    from taxiroute.certificados_lan import comprobar_autoridad_publica
    bloques = x509.load_pem_x509_certificates(Path(certificado).read_bytes())
    if len(bloques) == 1:
        ancla = certificado  # Compatibilidad con instalaciones autofirmadas.
    else:
        comprobar_autoridad_publica(certificado, direccion)
        ancla = Path(certificado).parent / 'autoridad.pem'
    contexto = ssl.create_default_context(cafile=str(ancla))
    cliente = build_opener(ProxyHandler({}), HTTPSHandler(context=contexto))
    limite = time.monotonic() + 30
    while True:
        try:
            with cliente.open(Request(f"https://{direccion}:{puerto}/acceso"), timeout=3) as respuesta:
                if respuesta.status == 200:
                    return
        except (OSError, ssl.SSLError):
            pass
        if time.monotonic() >= limite:
            raise ErrorArranqueRed("No respondió HTTPS con el certificado indicado.") from None
        time.sleep(0.5)


def arrancar_red(*, identidad, proyecto, direccion, puerto, datos, cert_interno,
                 clave_interna, cert_externo, clave_externa):
    """Crea composición e identidad nuevas; no reutiliza ni reemplaza servicios.

    Se conserva cualquier resultado parcial ante errores para revisión. La
    verificación no excluye procesos que ignoren el bloqueo cooperativo.
    """
    try:
        if not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto):
            raise ValueError("Nombre de proyecto no válido.")
        if type(puerto) is not int or not 1 <= puerto <= 65535:
            raise ValueError("Puerto no válido.")
        comprobar_direccion_local(direccion)
        rutas = {k: _ruta_existente(v, directorio=(k == "datos")) for k, v in {
            "datos": datos, "cert_interno": cert_interno, "clave_interna": clave_interna,
            "cert_externo": cert_externo, "clave_externa": clave_externa}.items()}
        if len(set(rutas.values())) != 5:
            raise ValueError("Rutas repetidas.")
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            archivo = rutas["datos"] / nombre
            if archivo.is_symlink() or not archivo.is_file():
                raise ValueError("Faltan datos.")
        identidad = Path(identidad)
        verificar_permisos_windows(identidad)
        verificar_permisos_windows(rutas["datos"])
        verificar_permisos_windows(rutas["cert_interno"].parent)
        verificar_permisos_windows(rutas["cert_externo"].parent)
        _validar_certificados(rutas["cert_interno"], rutas["clave_interna"], interno=True, direccion=direccion)
        verificar_certificado_externo(rutas["cert_externo"], rutas["clave_externa"], direccion)
        _claves_distintas(rutas["cert_interno"], rutas["cert_externo"])
        with _bloqueo_instalacion(identidad):
            _exigir_sin_actualizacion_pendiente(identidad)
            if _docker(["ps", "--quiet", "--no-trunc"]).strip():
                raise ValueError("Hay contenedores activos; requiere revisión.")
            with bloqueo_historial(rutas["datos"] / "servidor", espera=0):
                _validar(rutas["datos"])
            if any(os.path.lexists(identidad / nombre) for nombre in (ARCHIVO, MARCADOR)):
                raise ValueError("La identidad ya existe.")
            entorno = os.environ.copy()
            entorno.update(TAXIROUTE_RED_IP=direccion, TAXIROUTE_RED_PUERTO=str(puerto),
                TAXIROUTE_PROXY_DATOS=str(rutas["datos"]), TAXIROUTE_CERT_INTERNO=str(rutas["cert_interno"]),
                TAXIROUTE_CLAVE_INTERNA=str(rutas["clave_interna"]), TAXIROUTE_CERT_EXTERNO=str(rutas["cert_externo"]),
                TAXIROUTE_CLAVE_EXTERNA=str(rutas["clave_externa"]))
            _sin_contenedores(proyecto)
            _compose(["build"], proyecto, entorno)
            imagenes = {}
            for servicio in ("backend", "proxy"):
                imagen = _docker(["image", "inspect", f"{proyecto}-{servicio}", "--format", "{{.Id}}"]).decode("ascii").strip()
                if not _IMAGEN.fullmatch(imagen):
                    raise ValueError("Imagen no válida.")
                imagenes[servicio] = imagen
            if imagenes["backend"] == imagenes["proxy"]:
                raise ValueError("Imágenes repetidas.")
            _sin_contenedores(proyecto)
            if _docker(["ps", "--quiet", "--no-trunc"]).strip():
                raise ValueError("Los contenedores han cambiado.")
            comprobar_direccion_local(direccion)
            # El servidor necesita adquirir el bloqueo al iniciar. Se libera
            # antes de Compose; procesos ajenos pueden actuar en ese intervalo.
            with bloqueo_historial(rutas["datos"] / "servidor", espera=0):
                _validar(rutas["datos"])
            _compose(["up", "-d", "--no-build", "--no-recreate"], proyecto, entorno)
            evidencia = verificar_composicion_red(proyecto=proyecto, imagenes_esperadas=imagenes,
                rutas_esperadas=rutas, direccion=direccion, puerto=puerto)
            _esperar_https(rutas["cert_externo"], direccion, puerto)
            verificar_composicion_red(proyecto=proyecto, imagenes_esperadas=imagenes,
                rutas_esperadas=rutas, direccion=direccion, puerto=puerto, anterior=evidencia)
            crear_manifiesto_red(identidad, proyecto=proyecto, imagenes=imagenes,
                contenedores={"backend": evidencia.backend.identificador, "proxy": evidencia.proxy.identificador},
                rutas=rutas, direccion=direccion, puerto=puerto)
            return verificar_instalacion_red(identidad)
    except ErrorArranqueRed:
        raise
    except Exception:
        raise ErrorArranqueRed("No se pudo verificar el arranque de red; conserva cualquier resultado parcial.") from None
