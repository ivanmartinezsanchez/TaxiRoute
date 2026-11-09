"""Demostración integrada en contenedor: exclusivamente datos ficticios."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from werkzeug.serving import make_server

from taxiroute.demo_integrada import demo_persistente


def certificado_temporal(carpeta):
    """Genera un certificado local de prueba, nuevo en cada arranque."""
    carpeta = Path(carpeta)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TaxiRoute demo local")])
    ahora = datetime.now(timezone.utc)
    certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
        .sign(clave, hashes.SHA256()))
    ruta_clave = carpeta / "clave.pem"
    ruta_certificado = carpeta / "certificado.pem"
    ruta_clave.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ruta_clave.chmod(0o600)
    ruta_certificado.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
    return ruta_certificado, ruta_clave


def main():
    with ExitStack() as pila:
        temporal = pila.enter_context(TemporaryDirectory(prefix="taxiroute-certificado-"))
        certificado, clave = certificado_temporal(temporal)
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.minimum_version = ssl.TLSVersion.TLSv1_2
        contexto.load_cert_chain(certificado, clave)
        app = pila.enter_context(demo_persistente(
            Path.home() / ".taxiroute" / "demo-integrada",
            mostrar_codigo=lambda codigo: print(f"Código ficticio de administrador: {codigo}", flush=True)))
        control = app.extensions["control_periodico"]
        # Compose publica este puerto únicamente en el bucle local del equipo.
        servidor = make_server("0.0.0.0", 8767, app, ssl_context=contexto, threaded=True)
        print("DEMOSTRACIÓN: datos y credenciales ficticios; no usar datos reales.", flush=True)
        print("Acceso local con Compose: https://127.0.0.1:18767/", flush=True)
        control.iniciar()
        try:
            servidor.serve_forever()
        finally:
            servidor.server_close()
            control.detener()


if __name__ == "__main__":
    main()
