"""Ejecuta una integración Docker aislada usando exclusivamente datos ficticios.

Requiere entorno Python con dependencias, Docker Desktop y acceso para construir
imágenes. No publica puertos distintos a 127.0.0.1:18771; ese puerto debe estar
libre. Crea un proyecto con nombre aleatorio y borra solo sus recursos.
"""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "compose.proxy-local-experimental.yaml"


def certificado(carpeta, nombre, *, dns):
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    titular = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = datetime.now(timezone.utc)
    alternativas = [x509.IPAddress(ip_address("127.0.0.1"))]
    if dns:
        alternativas.append(x509.DNSName(dns))
    cert = (x509.CertificateBuilder().subject_name(titular).issuer_name(titular)
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - timedelta(minutes=1))
            .not_valid_after(ahora + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(alternativas), critical=False)
            .sign(clave, hashes.SHA256()))
    archivo_cert = carpeta / (nombre + ".pem")
    archivo_clave = carpeta / (nombre + ".key")
    archivo_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    archivo_clave.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return archivo_cert, archivo_clave


def main():
    nombre = "taxiroute-ensayo-" + uuid.uuid4().hex[:12]
    with TemporaryDirectory(prefix="taxiroute-ficticio-") as temporal:
        base = Path(temporal)
        datos = preparar_demo_sqlite(base / "datos")
        cuenta = crear_cuenta(datos / "cuentas.sqlite3", nombre_usuario="LuisDemo",
                              nombre_visible="Luis de prueba", contrasena="ConductorDemo123!")
        with conectar(datos / "cuentas.sqlite3") as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
            conexion.commit()
        interno, llave_i = certificado(base, "interno", dns="taxiroute-backend.local")
        externo, llave_e = certificado(base, "externo", dns=None)
        entorno = {**os.environ, "TAXIROUTE_PROXY_DATOS": str(datos),
                   "TAXIROUTE_CERT_INTERNO": str(interno), "TAXIROUTE_CLAVE_INTERNA": str(llave_i),
                   "TAXIROUTE_CERT_EXTERNO": str(externo), "TAXIROUTE_CLAVE_EXTERNA": str(llave_e)}
        compose = ["docker", "compose", "-p", nombre, "-f", str(COMPOSE)]
        try:
            subprocess.run(compose + ["up", "--build", "-d"], cwd=RAIZ, env=entorno, check=True)
            prueba = (RAIZ / "experimentos/proxy-local/prueba.py").read_text(encoding="utf-8")
            prueba = prueba.replace('"/prueba/certificado.pem"', repr(str(externo)))
            prueba = prueba.replace('"/prueba/clave.pem"', repr(str(llave_e)))
            subprocess.run([sys.executable, "-c", prueba], cwd=RAIZ, env=entorno, check=True)
        finally:
            subprocess.run(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"], cwd=RAIZ, env=entorno, check=True)


if __name__ == "__main__":
    main()
