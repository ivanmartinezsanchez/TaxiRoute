"""Recorrido HTTPS real en loopback con certificado y usuarios desechables."""
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
from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO, crear_demo_temporal


class PruebasHTTPSIntegrado(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        clave = ec.generate_private_key(ec.SECP256R1())
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        ahora = datetime.now(timezone.utc)
        certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(clave, hashes.SHA256()))
        cert, llave = carpeta / "cert.pem", carpeta / "key.pem"
        cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
        llave.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.minimum_version = ssl.TLSVersion.TLSv1_2
        contexto.load_cert_chain(cert, llave)
        self.buzon = []
        self.app = crear_demo_temporal(carpeta, mostrar_codigo=self.buzon.append)
        self.servidor = make_server("127.0.0.1", 0, self.app, ssl_context=contexto, threaded=True)
        hilo = Thread(target=self.servidor.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(hilo.join, 5)
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)
        self.base = f"https://127.0.0.1:{self.servidor.server_port}"
        confianza = ssl.create_default_context(cafile=str(cert))
        self.conductor = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=confianza))
        self.admin = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=confianza))

    def get(self, cliente, ruta):
        with cliente.open(self.base + ruta, timeout=5) as respuesta:
            return respuesta.read()

    def post(self, cliente, ruta, datos):
        req = Request(self.base + ruta, data=json.dumps(datos).encode(),
                      headers={"Content-Type": "application/json", "Origin": self.base}, method="POST")
        with cliente.open(req, timeout=5) as respuesta:
            return json.load(respuesta)

    def test_acceso_carrera_y_administracion_con_tls_validado(self):
        self.assertIn(b"form-acceso", self.get(self.conductor, "/acceso"))
        with self.assertRaises(HTTPError) as denegado:
            self.get(self.conductor, "/taximetro")
        self.assertEqual(denegado.exception.code, 401)
        self.post(self.conductor, "/acceso/conductor", {"usuario": "AnaDemo", "contrasena": CLAVE_CONDUCTOR_DEMO})
        self.assertIn(b"taximetro_seguro.js", self.get(self.conductor, "/taximetro"))
        self.assertTrue(self.post(self.conductor, "/api/carrera/iniciar", {"revision": 0})["activa"])
        inicio = self.post(self.admin, "/acceso/administrador", {"usuario": "AdminDemo", "contrasena": CLAVE_ADMIN_DEMO})
        self.assertEqual(inicio["paso"], "pin_pendiente")
        self.assertEqual(len(self.buzon), 1)
        self.post(self.admin, "/acceso/administrador/pin", {"codigo": self.buzon[0]})
        self.assertIn(b"Cuentas de conductores", self.get(self.admin, "/administracion"))
        self.post(self.admin, "/api/tarifas", {"parado": "0.03", "en_movimiento": "0.06"})
        self.post(self.conductor, "/api/carrera/finalizar", {"revision": 1})
        datos = json.loads(self.get(self.conductor, "/api/historial"))
        self.assertEqual(datos["carreras"][0]["tarifa_parado"], "0.02")
        self.post(self.conductor, "/api/carrera/iniciar", {"revision": 2})
        self.post(self.conductor, "/api/carrera/finalizar", {"revision": 3})
        datos = json.loads(self.get(self.conductor, "/api/historial"))
        self.assertEqual(datos["carreras"][1]["tarifa_parado"], "0.03")