
import os
from pathlib import Path
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
p = Path("/prueba")
d = preparar_demo_sqlite(p / "datos")
from taxiroute.cuentas import crear_cuenta
from taxiroute.base_cuentas import conectar
cuenta = crear_cuenta(d / "cuentas.sqlite3", nombre_usuario="LuisDemo",
                     nombre_visible="Luis de prueba", contrasena="ConductorDemo123!")
with conectar(d / "cuentas.sqlite3") as conexion:
    conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
    conexion.commit()
k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "taxiroute-backend.local")])
ahora = datetime.now(timezone.utc)
c = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
     .serial_number(x509.random_serial_number()).not_valid_before(ahora-timedelta(minutes=1))
     .not_valid_after(ahora+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([
       x509.DNSName("taxiroute-backend.local"),x509.IPAddress(ip_address("127.0.0.1"))]),critical=False)
     .sign(k,hashes.SHA256()))
cert, clave = p/"certificado.pem", p/"clave.pem"
cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
clave.write_bytes(k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
clave.chmod(0o600)

from taxiroute.servidor_gunicorn_experimental import crear_servidor_gunicorn
app = dict(ruta_cuentas=d/"cuentas.sqlite3", ruta_historial=d/"historial.sqlite3",
    ruta_tarifas=d/"tarifas.json", certificado=cert, clave=clave, puerto=18771,
    enviar_pin=lambda destino, codigo: None)
config = dict(escucha="127.0.0.1", puerto=18772, certificado=cert, clave=clave, aplicacion=app)
crear_servidor_gunicorn(config).run()
