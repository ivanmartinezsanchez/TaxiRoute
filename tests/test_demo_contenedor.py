"""Comprobación del certificado desechable de la demostración."""
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
import ssl
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from taxiroute.demo_contenedor import certificado_temporal


class PruebasCertificadoDemo(unittest.TestCase):
    def test_certificado_y_clave_sirven_para_tls_local(self):
        with TemporaryDirectory() as carpeta:
            certificado, clave = certificado_temporal(carpeta)
            self.assertEqual(certificado.parent, Path(carpeta))
            cert = x509.load_pem_x509_certificate(certificado.read_bytes())
            self.assertIn(ip_address("127.0.0.1"), cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress))
            self.assertLessEqual(cert.not_valid_before_utc, datetime.now(timezone.utc))
            self.assertGreater(cert.not_valid_after_utc, datetime.now(timezone.utc))
            privada = serialization.load_pem_private_key(clave.read_bytes(), password=None)
            self.assertEqual(cert.public_key().public_numbers(), privada.public_key().public_numbers())
            contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            contexto.load_cert_chain(certificado, clave)


if __name__ == "__main__":
    unittest.main()
