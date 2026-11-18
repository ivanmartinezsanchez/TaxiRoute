"""Demostración HTTPS local con certificado temporal de prueba y datos ficticios."""
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from ipaddress import ip_address
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from werkzeug.serving import make_server

from taxiroute.demo_local import crear_demo


class PruebasDemoLocal(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        carpeta = Path(self.temporal.name)
        clave = ec.generate_private_key(ec.SECP256R1())
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        ahora = datetime.now(timezone.utc)
        certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - timedelta(minutes=1))
            .not_valid_after(ahora + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(clave, hashes.SHA256()))
        cert = carpeta / "cert.pem"
        key = carpeta / "key.pem"
        cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
        key.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(cert, key)
        self.app = crear_demo(carpeta)
        self.servidor = make_server("127.0.0.1", 0, self.app, ssl_context=contexto, threaded=True)
        hilo = Thread(target=self.servidor.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(hilo.join, 5)
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)
        self.origen = f"https://127.0.0.1:{self.servidor.server_port}"
        confianza = ssl.create_default_context(cafile=str(cert))
        self.cliente = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=confianza))

    def post(self, ruta, datos):
        req = Request(self.origen + ruta, data=json.dumps(datos).encode(),
                      headers={"Content-Type": "application/json", "Origin": self.origen},
                      method="POST")
        with self.cliente.open(req, timeout=5) as respuesta:
            return json.load(respuesta)

    def test_recorrido_https_con_cookie_real_y_buzon_ficticio(self):
        with self.cliente.open(self.origen + "/", timeout=5) as respuesta:
            html = respuesta.read().decode()
        self.assertIn("AdminDemo", html)
        self.assertIn("No uses credenciales reales", html)
        with self.assertRaises(HTTPError) as sin_acceso:
            self.cliente.open(self.origen + "/panel", timeout=5)
        self.assertEqual(sin_acceso.exception.code, 401)
        inicio = self.post("/acceso/administrador",
            {"usuario": "AdminDemo", "contrasena": "DemoTaxiroute123!"})
        self.assertEqual(inicio["paso"], "pin_pendiente")
        codigo = inicio["codigo_demostracion"]
        self.assertRegex(codigo, r"^[0-9]{7}$")
        verificado = self.post("/acceso/administrador/pin", {"codigo": codigo})
        self.assertEqual(verificado["paso"], "acceso_completado")
        with self.cliente.open(self.origen + "/panel", timeout=5) as respuesta:
            self.assertIn("Acceso completado", respuesta.read().decode())

    def test_rechaza_origen_externo(self):
        req = Request(self.origen + "/acceso/administrador",
            data=b"{}", headers={"Content-Type": "application/json", "Origin": "https://otro.example"},
            method="POST")
        with self.assertRaises(HTTPError) as error:
            self.cliente.open(req, timeout=5)
        self.assertEqual(error.exception.code, 403)
