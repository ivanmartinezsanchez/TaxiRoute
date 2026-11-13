"""Pruebas de primer arranque de proxy sin credenciales ni Docker."""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from taxiroute.arranque_proxy_experimental import (
    ErrorArranqueProxy, _claves_distintas, _validar_certificados, arrancar_proxy,
)
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


class PruebasArranqueProxy(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory(prefix="arranque-proxy-")
        self.addCleanup(t.cleanup)
        base = Path(t.name)
        self.datos = base / "datos"
        self.datos.mkdir()
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            (self.datos / nombre).write_text("ficticio", encoding="utf-8")
        self.rutas = {}
        for nombre in ("cert_interno", "clave_interna", "cert_externo", "clave_externa"):
            archivo = base / nombre
            archivo.write_text(nombre, encoding="utf-8")
            self.rutas[nombre] = archivo
        self.imagenes = {"backend": "sha256:" + "a"*64, "proxy": "sha256:" + "b"*64}
        self.evidencia = EstadoComposicion(
            EstadoContenedor("1"*64, self.imagenes["backend"], "2026-11-13T00:00:00Z", 0),
            EstadoContenedor("2"*64, self.imagenes["proxy"], "2026-11-13T00:00:00Z", 0),
        )
        self.parches = []
        for nombre in ("_validar_certificados", "_claves_distintas", "_componer", "_esperar_https", "verificar_composicion", "_docker"):
            parche = patch("taxiroute.arranque_proxy_experimental." + nombre)
            setattr(self, nombre, parche.start())
            self.addCleanup(parche.stop)
        self.verificar_composicion.return_value = self.evidencia
        self._docker.side_effect = [b"", self.imagenes["backend"].encode(),
                                   self.imagenes["proxy"].encode(), b""]

    def arrancar(self):
        return arrancar_proxy(datos=self.datos, proyecto="taxiroute-ensayo", **self.rutas)

    def test_arranca_nuevo_y_comprueba_ambos_servicios(self):
        resultado = self.arrancar()
        self.assertEqual(resultado.evidencia, self.evidencia)
        self.assertEqual(resultado.imagen_backend, self.imagenes["backend"])
        self.assertEqual(resultado.imagen_proxy, self.imagenes["proxy"])
        self.assertEqual([c.args[0] for c in self._componer.call_args_list],
                         [["build"], ["up", "-d", "--no-build", "--no-recreate"]])
        self.assertEqual(self.verificar_composicion.call_count, 2)
        self.assertEqual(self.verificar_composicion.call_args.kwargs["anterior"], self.evidencia)
        self._esperar_https.assert_called_once()

    def test_existente_no_se_construye_ni_se_detiene(self):
        self._docker.side_effect = [b"contenedor"]
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self._componer.assert_not_called()
        self.verificar_composicion.assert_not_called()

    def test_contenedor_aparece_tras_build_no_se_arranca(self):
        self._docker.side_effect = [b"", self.imagenes["backend"].encode(),
                                    self.imagenes["proxy"].encode(), b"nuevo"]
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self.assertEqual([c.args[0] for c in self._componer.call_args_list], [["build"]])

    def test_ids_invalidos_no_arrancan(self):
        self._docker.side_effect = [b"", b"etiqueta"]
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self.assertEqual([c.args[0] for c in self._componer.call_args_list], [["build"]])

    def test_faltan_datos_y_certificados_iguales_no_inician(self):
        (self.datos / "cuentas.sqlite3").unlink()
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self._docker.assert_not_called()
        (self.datos / "cuentas.sqlite3").write_text("ficticio", encoding="utf-8")
        self._claves_distintas.side_effect = ValueError("misma clave")
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self._docker.assert_not_called()

    def test_error_https_no_detiene_ni_recrea(self):
        self._esperar_https.side_effect = ValueError("fallo")
        with self.assertRaises(ErrorArranqueProxy):
            self.arrancar()
        self.assertEqual([c.args[0] for c in self._componer.call_args_list],
                         [["build"], ["up", "-d", "--no-build", "--no-recreate"]])


class PruebasCertificados(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory(prefix="proxy-tls-")
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)

    def certificado(self, *, dns, dias):
        clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "prueba")])
        ahora = datetime.now(timezone.utc)
        alt = [x509.IPAddress(ip_address("127.0.0.1"))]
        if dns:
            alt.append(x509.DNSName(dns))
        certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora-timedelta(days=3))
            .not_valid_after(ahora+timedelta(days=dias))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False).sign(clave, hashes.SHA256()))
        cert, key = self.base / "cert.pem", self.base / "key.pem"
        cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
        key.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return cert, key

    def test_rechaza_certificados_distintos_con_la_misma_clave(self):
        clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "prueba")])
        ahora = datetime.now(timezone.utc)
        archivos = []
        for indice in (1, 2):
            cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                .public_key(clave.public_key()).serial_number(indice)
                .not_valid_before(ahora-timedelta(days=1))
                .not_valid_after(ahora+timedelta(days=1)).sign(clave, hashes.SHA256()))
            archivo = self.base / f"{indice}.pem"
            archivo.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            archivos.append(archivo)
        self.assertNotEqual(archivos[0].read_bytes(), archivos[1].read_bytes())
        with self.assertRaises(ValueError):
            _claves_distintas(*archivos)

    def test_san_y_vigencia(self):
        cert, clave = self.certificado(dns="taxiroute-backend.local", dias=1)
        _validar_certificados(cert, clave, interno=True)
        cert, clave = self.certificado(dns=None, dias=1)
        _validar_certificados(cert, clave, interno=False)
        with self.assertRaises(ValueError):
            _validar_certificados(cert, clave, interno=True)
        cert, clave = self.certificado(dns=None, dias=-1)
        with self.assertRaises(ValueError):
            _validar_certificados(cert, clave, interno=False)


if __name__ == "__main__":
    unittest.main()
