"""Prepara una instalación SQLite nueva sin adoptar ni sobrescribir datos."""
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from ipaddress import ip_address
import os
from pathlib import Path
import ssl
import unicodedata

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cuentas import normalizar_usuario, validar_contrasena
from taxiroute.historial import CAMPOS
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.importacion_flota_csv import activar_flota_sqlite
from taxiroute.inicializacion_administrador import preparar_primer_administrador_con_codigos
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado

MARCADOR = "TaxiRoute: instalación SQLite local, versión 1.\n"
ARCHIVO_MARCADOR = "INSTALACION_TAXIROUTE.txt"


@dataclass(frozen=True)
class RutasInstalacion:
    carpeta: Path
    datos: Path
    certificado: Path
    clave: Path


def _es_redireccion(ruta):
    """Detecta enlaces simbólicos y uniones de directorio conocidas por Python."""
    ruta = Path(ruta)
    return ruta.is_symlink() or ruta.is_junction()


def _comprobar_datos(usuario, nombre, correo, contrasena, confirmar_codigos):
    normalizar_usuario(usuario)
    validar_contrasena(contrasena)
    if not isinstance(nombre, str):
        raise ValueError("El nombre visible no es válido.")
    nombre_normal = unicodedata.normalize("NFC", nombre).strip()
    if not 1 <= len(nombre_normal) <= 100 or any(unicodedata.category(c).startswith("C") for c in nombre_normal):
        raise ValueError("El nombre visible no es válido.")
    if not isinstance(correo, str) or len(correo) > 254:
        raise ValueError("El correo no es válido.")
    alias, direccion = parseaddr(correo, strict=True)
    if (alias or direccion != correo or direccion.count("@") != 1
            or not all(direccion.split("@")) or any(c in correo for c in "\r\n")):
        raise ValueError("El correo no es válido.")
    if not callable(confirmar_codigos):
        raise ValueError("Es necesario confirmar los códigos de recuperación.")


def _generar_certificado(carpeta):
    """Certificado autofirmado local; no establece confianza en el navegador."""
    carpeta.mkdir(mode=0o700)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TaxiRoute local")])
    ahora = datetime.now(timezone.utc)
    certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(clave, hashes.SHA256()))
    ruta_clave, ruta_certificado = carpeta / "clave.pem", carpeta / "certificado.pem"
    descriptor = os.open(ruta_clave, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with ruta_certificado.open("xb") as archivo:
        archivo.write(certificado.public_bytes(serialization.Encoding.PEM))
    return ruta_certificado, ruta_clave


def preparar_instalacion(carpeta, *, usuario, nombre, correo, contrasena, confirmar_codigos):
    """Crea solo una instalación nueva. Si falla, conserva la carpeta parcial.

    No inicia la app, ni envía correos, ni configura OAuth. En Windows los
    permisos de la carpeta y clave requieren comprobación adicional.
    """
    _comprobar_datos(usuario, nombre, correo, contrasena, confirmar_codigos)
    solicitada = Path(carpeta).expanduser()
    if solicitada.name in ("", ".", ".."):
        raise ValueError("Elige una carpeta nueva para TaxiRoute.")
    padre_solicitado = solicitada.absolute().parent
    if any(_es_redireccion(p) for p in (padre_solicitado, *padre_solicitado.parents)):
        raise ValueError("La ruta de instalación contiene un enlace o una unión.")
    padre = padre_solicitado.resolve(strict=True)
    if not padre.is_dir() or any((p / ".git").exists() or (p / ".git").is_symlink() for p in (padre, *padre.parents)):
        raise ValueError("La instalación debe estar fuera del repositorio.")
    destino = padre / solicitada.name
    destino.mkdir(mode=0o700)
    archivos = preparar_archivos_csv(destino / "datos")
    admin = preparar_primer_administrador_con_codigos(archivos.cuentas,
        nombre_usuario=usuario, nombre_visible=nombre, contrasena=contrasena,
        correo=correo, confirmar_codigos=confirmar_codigos)
    carpeta_admin = archivos.historiales / admin.cuenta.id
    carpeta_admin.mkdir(mode=0o700)
    with (carpeta_admin / "historial.csv").open("x", encoding="utf-8", newline="") as archivo:
        csv.DictWriter(archivo, fieldnames=CAMPOS, lineterminator="\n").writeheader()
    historial = archivos.carpeta / "historial.sqlite3"
    HistorialSQLite(historial).inicializar()
    activar_flota_sqlite(archivos.cuentas, archivos.historiales, historial)
    with bloqueo_historial(archivos.carpeta / "servidor", espera=0):
        verificar_historial_sqlite_vinculado(archivos.cuentas, historial)
    certificado, clave = _generar_certificado(destino / "tls")
    with (destino / ARCHIVO_MARCADOR).open("x", encoding="utf-8", newline="\n") as archivo:
        archivo.write(MARCADOR)
    return RutasInstalacion(destino, archivos.carpeta, certificado, clave)


def revisar_instalacion(carpeta):
    """Revisión preliminar de marcador, archivos y TLS; no verifica las bases.

    El servidor comprueba los datos y su vínculo bajo bloqueo antes de usarlos.
    """
    carpeta = Path(carpeta).expanduser().absolute()
    if any(_es_redireccion(p) for p in (carpeta, *carpeta.parents)) or not carpeta.is_dir():
        raise ValueError("No hay una instalación válida en la carpeta indicada.")
    carpeta = carpeta.resolve(strict=True)
    if any((p / ".git").exists() or (p / ".git").is_symlink() for p in (carpeta, *carpeta.parents)):
        raise ValueError("La instalación debe estar fuera del repositorio.")
    marcador = carpeta / ARCHIVO_MARCADOR
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != MARCADOR:
        raise ValueError("La carpeta está incompleta o no es una instalación reconocida.")
    datos, tls = carpeta / "datos", carpeta / "tls"
    if any(_es_redireccion(p) or not p.is_dir() for p in (datos, tls)):
        raise ValueError("Las carpetas de datos o TLS necesitan revisión.")
    cuentas, historial, tarifas = (datos / nombre for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"))
    certificado, clave = tls / "certificado.pem", tls / "clave.pem"
    if any(p.is_symlink() or not p.is_file() for p in (cuentas, historial, tarifas, certificado, clave)):
        raise ValueError("Faltan archivos de la instalación o hay enlaces no permitidos.")
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(certificado, clave)
    cert = x509.load_pem_x509_certificate(certificado.read_bytes())
    ahora = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= ahora <= cert.not_valid_after_utc:
        raise ValueError("El certificado local ha caducado o aún no es válido.")
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ip_address("127.0.0.1") not in san.get_values_for_type(x509.IPAddress):
            raise ValueError("El certificado no corresponde al acceso local.")
    except x509.ExtensionNotFound:
        raise ValueError("El certificado no identifica el acceso local.") from None
    return RutasInstalacion(carpeta, datos, certificado, clave)
