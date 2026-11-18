"""Emisión de un certificado de red y su autoridad privada local.

La clave de la autoridad no se entrega a clientes ni se incorpora a imágenes.
La comprobación ACL es una fotografía y no protege frente al administrador.
"""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from taxiroute.servidor_sqlite_local import _validar_direccion


def _escribir_exclusivo(ruta, datos, modo=0o600):
    descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, modo)
    with os.fdopen(descriptor, 'wb') as salida:
        salida.write(datos)
        salida.flush()
        os.fsync(salida.fileno())


def generar_certificado_lan(carpeta_tls, carpeta_ca, *, direccion, comprobar_permisos):
    """Crea claves nuevas en carpetas separadas; conserva parciales al fallar.

    El archivo autoridad.cer es público. certificado.pem contiene hoja y CA
    pública; solo la carpeta privada CA contiene la clave firmante.
    """
    ip = _validar_direccion(direccion)
    if str(ip) == '127.0.0.1' or not callable(comprobar_permisos):
        raise ValueError('Se requiere una IPv4 privada y comprobación de permisos.')
    carpeta_tls, carpeta_ca = Path(carpeta_tls), Path(carpeta_ca)
    if carpeta_tls.absolute() == carpeta_ca.absolute():
        raise ValueError('Las carpetas deben ser distintas.')
    for carpeta in (carpeta_tls, carpeta_ca):
        comprobar_permisos(carpeta, permitir_ausente=True)
        carpeta.mkdir(mode=0o700)
        comprobar_permisos(carpeta)
    ahora = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TaxiRoute autoridad local')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora-timedelta(minutes=5)).not_valid_after(ahora+timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TaxiRoute red local')])
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(ca.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora-timedelta(minutes=5)).not_valid_after(ahora+timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1')),
                                                    x509.IPAddress(ip)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256()))
    formato = serialization.PrivateFormat.PKCS8
    encoding = serialization.Encoding.PEM
    _escribir_exclusivo(carpeta_ca/'autoridad.key', ca_key.private_bytes(encoding, formato, serialization.NoEncryption()))
    comprobar_permisos(carpeta_ca)
    _escribir_exclusivo(carpeta_tls/'clave.pem', key.private_bytes(encoding, formato, serialization.NoEncryption()))
    _escribir_exclusivo(carpeta_tls/'certificado.pem', cert.public_bytes(encoding)+ca.public_bytes(encoding))
    _escribir_exclusivo(carpeta_tls/'autoridad.cer', ca.public_bytes(serialization.Encoding.DER))
    _escribir_exclusivo(carpeta_tls/'autoridad.pem', ca.public_bytes(encoding))
    comprobar_permisos(carpeta_tls)
    comprobar_permisos(carpeta_ca)
    return carpeta_tls/'certificado.pem', carpeta_tls/'clave.pem'


def validar_cadena_lan(certificado, direccion):
    """Comprueba hoja, emisor, firma y CA pública en la cadena PEM exacta."""
    from cryptography.hazmat.primitives.asymmetric import padding
    from datetime import datetime, timezone
    bloques=x509.load_pem_x509_certificates(Path(certificado).read_bytes())
    if len(bloques)!=2:
        raise ValueError('Se requieren hoja y autoridad pública.')
    hoja, ca=bloques
    ahora=datetime.now(timezone.utc)
    if not (hoja.not_valid_before_utc <= ahora < hoja.not_valid_after_utc
            and ca.not_valid_before_utc <= ahora < ca.not_valid_after_utc):
        raise ValueError('Certificado fuera de vigencia.')
    if (not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or hoja.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
            or hoja.issuer != ca.subject or ca.issuer != ca.subject):
        raise ValueError('Autoridad o emisor no válidos.')
    san=hoja.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if _validar_direccion(direccion) not in san.get_values_for_type(x509.IPAddress):
        raise ValueError('La dirección no coincide.')
    if ExtendedKeyUsageOID.SERVER_AUTH not in hoja.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
        raise ValueError('El certificado no está habilitado para servidor.')
    ca.public_key().verify(ca.signature, ca.tbs_certificate_bytes, padding.PKCS1v15(), ca.signature_hash_algorithm)
    ca.public_key().verify(hoja.signature, hoja.tbs_certificate_bytes, padding.PKCS1v15(), hoja.signature_hash_algorithm)
    return ca


def verificar_certificado_externo(certificado, clave, direccion):
    """Valida el certificado LAN y su CA si contiene una cadena; acepta legado.

    Los certificados legados autofirmados siguen requiriendo otra solución de
    confianza en los clientes. No instala una autoridad en ningún dispositivo.
    """
    from taxiroute.arranque_proxy_experimental import _validar_certificados
    certificado = Path(certificado)
    _validar_certificados(certificado, clave, interno=False, direccion=direccion)
    bloques = x509.load_pem_x509_certificates(certificado.read_bytes())
    if len(bloques) == 1:
        return False
    comprobar_autoridad_publica(certificado, direccion)
    return True


def comprobar_autoridad_publica(certificado, direccion):
    """Valida la cadena y exige que los dos archivos públicos coincidan."""
    certificado = Path(certificado)
    ca = validar_cadena_lan(certificado, direccion)
    publico = certificado.parent / 'autoridad.cer'
    if publico.is_symlink() or not publico.is_file():
        raise ValueError('Falta el certificado público de autoridad.')
    pem = certificado.parent / 'autoridad.pem'
    if pem.is_symlink() or not pem.is_file():
        raise ValueError('Falta la autoridad pública PEM.')
    esperada = ca.fingerprint(hashes.SHA256())
    if (x509.load_der_x509_certificate(publico.read_bytes()).fingerprint(hashes.SHA256()) != esperada
            or x509.load_pem_x509_certificate(pem.read_bytes()).fingerprint(hashes.SHA256()) != esperada):
        raise ValueError('El certificado público de autoridad no coincide.')
    return True


_ARCHIVOS_PREPARADOS = (
    ('tls', 'certificado.pem'), ('tls', 'clave.pem'), ('tls', 'autoridad.cer'),
    ('tls', 'autoridad.pem'), ('autoridad-privada', 'autoridad.key'),
)


def verificar_certificados_preparados(carpeta, *, direccion, comprobar_permisos):
    """Comprueba material previo y que la clave de CA corresponda a la raíz."""
    carpeta = Path(carpeta).expanduser().absolute()
    comprobar_permisos(carpeta)
    for sub, nombre in _ARCHIVOS_PREPARADOS:
        archivo = carpeta/sub/nombre
        if archivo.is_symlink() or not archivo.is_file():
            raise ValueError('Falta material de certificados o contiene enlaces.')
    certificado, clave = carpeta/'tls'/'certificado.pem', carpeta/'tls'/'clave.pem'
    if not verificar_certificado_externo(certificado, clave, direccion):
        raise ValueError('Se necesita una autoridad pública válida.')
    ca = validar_cadena_lan(certificado, direccion)
    privada = serialization.load_pem_private_key((carpeta/'autoridad-privada'/'autoridad.key').read_bytes(), password=None)
    formato = serialization.PublicFormat.SubjectPublicKeyInfo
    encoding = serialization.Encoding.DER
    if privada.public_key().public_bytes(encoding, formato) != ca.public_key().public_bytes(encoding, formato):
        raise ValueError('La clave de la autoridad no corresponde a su certificado.')
    return certificado, clave


def copiar_certificados_preparados(carpeta, destino, *, direccion, comprobar_permisos):
    """Copia material previo verificado a carpetas nuevas sin sobrescribir.

    La clave de autoridad se conserva solo en el equipo, nunca se exporta a
    los dispositivos ni se monta dentro del contenedor.
    """
    carpeta, destino = Path(carpeta).resolve(strict=True), Path(destino).resolve(strict=True)
    if carpeta == destino or carpeta in destino.parents or destino in carpeta.parents:
        raise ValueError('Las carpetas de certificados deben estar separadas.')
    verificar_certificados_preparados(carpeta, direccion=direccion, comprobar_permisos=comprobar_permisos)
    for sub in ('tls', 'autoridad-privada'):
        carpeta_nueva = destino/sub
        comprobar_permisos(carpeta_nueva, permitir_ausente=True)
        carpeta_nueva.mkdir(mode=0o700)
        comprobar_permisos(carpeta_nueva)
    import hashlib
    huellas = {}
    for sub, nombre in _ARCHIVOS_PREPARADOS:
        archivo = carpeta/sub/nombre
        contenido = archivo.read_bytes()
        huellas[(sub,nombre)] = hashlib.sha256(contenido).digest()
        _escribir_exclusivo(destino/sub/nombre, contenido)
        contenido = None
    comprobar_permisos(destino)
    verificar_certificados_preparados(destino, direccion=direccion, comprobar_permisos=comprobar_permisos)
    verificar_certificados_preparados(carpeta, direccion=direccion, comprobar_permisos=comprobar_permisos)
    for sub, nombre in _ARCHIVOS_PREPARADOS:
        if hashlib.sha256((carpeta/sub/nombre).read_bytes()).digest() != huellas[(sub,nombre)]:
            raise ValueError('El material de origen ha cambiado durante la copia.')
        if hashlib.sha256((destino/sub/nombre).read_bytes()).digest() != huellas[(sub,nombre)]:
            raise ValueError('El material copiado no coincide.')
    return destino/'tls'/'certificado.pem', destino/'tls'/'clave.pem'
