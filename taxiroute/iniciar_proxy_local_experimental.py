"""Entrada experimental para una instalación nueva con proxy local.

No migra instalaciones anteriores ni envía correos. El almacén de Gmail solo
se consulta con una opción explícita; aún no está validado para datos reales.
"""
import argparse
from datetime import datetime, timedelta, timezone
import getpass
from ipaddress import ip_address
import os
from pathlib import Path
import sqlite3
import sys
import webbrowser

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from taxiroute.arranque_proxy_experimental import PROYECTO
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.carga_proxy_experimental import CargaProxyPosibleExposicion
from taxiroute.iniciar import _crear
from taxiroute.inicializacion_administrador import InicializacionCancelada
from taxiroute.instalacion_inicial import revisar_instalacion
from taxiroute.manifiesto_proxy_experimental import ARCHIVO, MARCADOR
from taxiroute.oauth_gmail import CLAVE_ALMACEN_ALTERNATIVA, SERVICIO_ALMACEN, _almacen_windows
from taxiroute.permisos_proxy_windows_experimental import PermisosProxyError, verificar_permisos_windows
from taxiroute.reutilizacion_proxy_experimental import ErrorReutilizacion, iniciar_y_registrar, reutilizar

URL = "https://127.0.0.1:18771/"


def _comprobar_docker():
    try:
        _docker(["version", "--format", "{{.Server.Version}}"])
    except Exception:
        raise ValueError("Docker Desktop no está disponible.") from None


def _leer_correo(carpeta):
    """Consulta el perfil autorizado solo después de comprobar los permisos."""
    verificar_permisos_windows(carpeta)
    contenido = _almacen_windows().get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN_ALTERNATIVA)
    if not isinstance(contenido, str) or not contenido:
        raise ValueError("El perfil de correo no está disponible.")
    return contenido


def _carpeta_predeterminada():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ValueError("No se encuentra la carpeta local de Windows.")
    return Path(base) / "TaxiRoute-Proxy-MVP"


def _crear_tls_interno(carpeta):
    """Crea un par nuevo; conserva cualquier preparación parcial al fallar."""
    carpeta.mkdir(mode=0o700)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "taxiroute-backend.local")])
    ahora = datetime.now(timezone.utc)
    certificado = (x509.CertificateBuilder().subject_name(sujeto).issuer_name(sujeto)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("taxiroute-backend.local"),
            x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(clave, hashes.SHA256()))
    ruta_cert, ruta_clave = carpeta / "interno.pem", carpeta / "interno.key"
    descriptor = os.open(ruta_clave, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with ruta_cert.open("xb") as archivo:
        archivo.write(certificado.public_bytes(serialization.Encoding.PEM))
    return ruta_cert, ruta_clave


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inicia una instalación proxy local experimental.")
    parser.add_argument("--carpeta", type=Path, help="Carpeta nueva o ya preparada por este lanzador")
    parser.add_argument("--proyecto", help="Nombre Docker solo para el primer arranque")
    parser.add_argument("--conectar-gmail", action="store_true", help="Cargar el perfil Gmail autorizado en la instalación verificada")
    parser.add_argument("--no-abrir", action="store_true", help="No abrir el navegador")
    args = parser.parse_args(argv)
    try:
        carpeta = args.carpeta if args.carpeta is not None else _carpeta_predeterminada()
        print("TaxiRoute experimental: utiliza únicamente datos de prueba.")
        # Antes de pedir contraseñas o acceder a una instalación existente.
        verificar_permisos_windows(carpeta, permitir_ausente=True)
        if os.path.lexists(carpeta):
            if args.proyecto is not None:
                raise ValueError("El nombre de una instalación existente no se modifica al reutilizarla.")
            rutas = revisar_instalacion(carpeta)
            identidad = rutas.carpeta / "proxy-local"
            if (identidad.is_symlink() or not identidad.is_dir()
                    or not (identidad / ARCHIVO).is_file() or not (identidad / MARCADOR).is_file()):
                raise ValueError("La instalación no tiene una identidad proxy completa; requiere revisión.")
        else:
            rutas = _crear(carpeta, comprobar_requisitos=_comprobar_docker)
            identidad = rutas.carpeta / "proxy-local"
            identidad.mkdir(mode=0o700)
            certificado, clave = _crear_tls_interno(identidad / "tls-interno")
            verificar_permisos_windows(carpeta)
            iniciar_y_registrar(identidad, proyecto=args.proyecto or PROYECTO, rutas={
                "datos": rutas.datos, "cert_interno": certificado, "clave_interna": clave,
                "cert_externo": rutas.certificado, "clave_externa": rutas.clave})
        verificar_permisos_windows(carpeta)
        print("Comprobando servicios y conexión HTTPS; puede tardar unos segundos.", flush=True)
        resultado = (reutilizar(identidad, proveedor=lambda: _leer_correo(carpeta))
                     if args.conectar_gmail else reutilizar(identidad))
        if resultado.servicios_recuperados:
            print("Se han recuperado los servicios locales de TaxiRoute.")
        if resultado.correo == "cargado":
            print("Perfil de Gmail cargado en la instalación local verificada.")
        elif resultado.correo == "ausente":
            print("Correo administrativo no conectado en esta variante experimental.")
        else:
            print("Hay un archivo de correo temporal; no se ha comprobado su identidad o vigencia.")
        print("Acceso local:", URL)
        print("El certificado es autofirmado y el navegador puede mostrar una advertencia.")
        if not args.no_abrir and not webbrowser.open(URL):
            print("No se ha podido abrir el navegador automáticamente.")
        return 0
    except (KeyboardInterrupt, EOFError, InicializacionCancelada):
        print("Operación cancelada. Conserva cualquier carpeta parcial para revisión.", file=sys.stderr)
        return 130
    except ErrorReutilizacion as error:
        print(str(error), file=sys.stderr)
        return 1
    except CargaProxyPosibleExposicion:
        print("No se pudo confirmar la carga de correo; pudo haberse transmitido. No reintentes sin revisar la instalación.", file=sys.stderr)
        return 1
    except PermisosProxyError:
        print("No se pueden comprobar los permisos de la instalación. Conserva la carpeta y revisa sus permisos.", file=sys.stderr)
        return 1
    except (getpass.GetPassWarning, OSError, ValueError, sqlite3.Error):
        print("No se pudo iniciar. Conserva la instalación y revisa su estado antes de reintentar.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
