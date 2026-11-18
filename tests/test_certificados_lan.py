from pathlib import Path
from tempfile import TemporaryDirectory
import socket
import ssl
import threading
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from taxiroute.certificados_lan import (generar_certificado_lan, verificar_certificado_externo,
    verificar_certificados_preparados, copiar_certificados_preparados)


class PruebasCertificadosLAN(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix='taxi-ca-')
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name).resolve()
        self.raiz = self.base/'material'
        self.raiz.mkdir()
        self.tls = self.raiz/'tls'
        self.ca = self.raiz/'autoridad-privada'
        self.cert, self.clave = generar_certificado_lan(self.tls, self.ca,
            direccion='192.168.1.25', comprobar_permisos=lambda *a, **k: None)

    def test_cadena_correcta_y_solo_certificado_publico_en_tls(self):
        self.assertTrue(verificar_certificado_externo(self.cert, self.clave, '192.168.1.25'))
        self.assertTrue((self.ca/'autoridad.key').is_file())
        self.assertFalse((self.tls/'autoridad.key').exists())
        self.assertNotIn(b'PRIVATE KEY', (self.tls/'autoridad.cer').read_bytes())
        with self.assertRaises(ValueError):
            verificar_certificado_externo(self.cert, self.clave, '192.168.1.26')

    def test_raiz_publica_ajena_es_rechazada(self):
        otro = self.raiz/'otra'
        tls = self.raiz/'otro-tls'
        generar_certificado_lan(tls, otro, direccion='192.168.1.25', comprobar_permisos=lambda *a, **k: None)
        (self.tls/'autoridad.pem').write_bytes((tls/'autoridad.pem').read_bytes())
        with self.assertRaises(ValueError):
            verificar_certificado_externo(self.cert, self.clave, '192.168.1.25')

    def test_handshake_tls_solo_confia_en_la_raiz_correcta(self):
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(str(self.cert), str(self.clave))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        puerto = listener.getsockname()[1]
        fallo = []
        def servidor():
            try:
                conexion, _ = listener.accept()
                with conexion, contexto.wrap_socket(conexion, server_side=True) as cliente:
                    cliente.sendall(b'ok')
            except Exception as exc:
                fallo.append(exc)
            finally:
                listener.close()
        hilo = threading.Thread(target=servidor, daemon=True)
        hilo.start()
        cliente = ssl.create_default_context(cafile=str(self.tls/'autoridad.pem'))
        with socket.create_connection(('127.0.0.1', puerto), timeout=5) as conexion:
            with cliente.wrap_socket(conexion, server_hostname='127.0.0.1') as seguro:
                self.assertEqual(seguro.recv(2), b'ok')
        hilo.join(5)
        self.assertFalse(hilo.is_alive())
        self.assertFalse(fallo)

    def test_handshake_rechaza_autoridad_ajena(self):
        otro, tls = self.raiz/'otra-ca', self.raiz/'otro-tls'
        generar_certificado_lan(tls, otro, direccion='192.168.1.25', comprobar_permisos=lambda *a, **k: None)
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(str(self.cert), str(self.clave))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        puerto = listener.getsockname()[1]
        def servidor():
            try:
                conexion, _ = listener.accept()
                with conexion:
                    with contexto.wrap_socket(conexion, server_side=True):
                        pass
            except ssl.SSLError:
                pass
            finally:
                listener.close()
        hilo = threading.Thread(target=servidor, daemon=True)
        hilo.start()
        cliente = ssl.create_default_context(cafile=str(tls/'autoridad.pem'))
        with socket.create_connection(('127.0.0.1', puerto), timeout=5) as conexion:
            with self.assertRaises(ssl.SSLCertVerificationError):
                cliente.wrap_socket(conexion, server_hostname='127.0.0.1')
        hilo.join(5)
        self.assertFalse(hilo.is_alive())

    def test_copiar_material_preparado_conserva_raiz_y_servidor(self):
        destino = self.base/'destino'
        destino.mkdir()
        cert, clave = copiar_certificados_preparados(self.raiz, destino, direccion='192.168.1.25',
            comprobar_permisos=lambda *a, **k: None)
        self.assertTrue(verificar_certificado_externo(cert, clave, '192.168.1.25'))
        self.assertEqual((self.tls/'autoridad.cer').read_bytes(), (destino/'tls'/'autoridad.cer').read_bytes())
        self.assertEqual(self.cert.read_bytes(), cert.read_bytes())
        with self.assertRaises(FileExistsError):
            copiar_certificados_preparados(self.raiz, destino, direccion='192.168.1.25',
                comprobar_permisos=lambda *a, **k: None)

    def test_clave_ca_ajena_impide_copiar(self):
        otro, tls = self.raiz/'otra-ca', self.raiz/'otro-tls'
        generar_certificado_lan(tls, otro, direccion='192.168.1.25', comprobar_permisos=lambda *a, **k: None)
        (self.ca/'autoridad.key').write_bytes((otro/'autoridad.key').read_bytes())
        with self.assertRaises(ValueError):
            verificar_certificados_preparados(self.raiz, direccion='192.168.1.25',
                comprobar_permisos=lambda *a, **k: None)

    def test_direccion_publica_no_crea_carpetas(self):
        tls, ca = self.raiz/'prohibido', self.raiz/'ca-prohibida'
        with self.assertRaises(ValueError):
            generar_certificado_lan(tls, ca, direccion='8.8.8.8', comprobar_permisos=lambda *a, **k: None)
        self.assertFalse(tls.exists())
        self.assertFalse(ca.exists())
