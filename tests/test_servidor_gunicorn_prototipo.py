"""Pruebas aisladas del prototipo; no requieren Gunicorn ni correo real."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import Mock, patch
import sys
import unittest

from taxiroute.bloqueo import HistorialOcupado
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.servidor_gunicorn_experimental import abrir_app_gunicorn, crear_servidor_gunicorn


class PruebasAplicacionPrototipo(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(); self.addCleanup(temporal.cleanup)
        raiz = Path(temporal.name)
        datos = preparar_demo_sqlite(raiz / "datos")
        certificado, clave = certificado_temporal(raiz)
        self.opciones = dict(ruta_cuentas=datos / "cuentas.sqlite3", ruta_historial=datos / "historial.sqlite3",
            ruta_tarifas=datos / "tarifas.json", certificado=certificado, clave=clave,
            puerto=18772, enviar_pin=lambda *_: None)

    def test_retiene_bloqueo_y_limita_host_origen(self):
        with abrir_app_gunicorn(**self.opciones) as app:
            self.assertTrue(app.config["RECUPERACION_HABILITADA"])
            with self.assertRaises(HistorialOcupado):
                with abrir_app_gunicorn(**self.opciones): pass
            cliente=app.test_client(); base="https://127.0.0.1:18772"
            self.assertEqual(cliente.get("/api/historial",base_url=base).status_code,401)
            self.assertEqual(cliente.get("/api/historial",base_url="https://127.0.0.1:18773").status_code,400)
            self.assertEqual(cliente.get("/api/historial",base_url="https://localhost:18772").status_code,400)
            cuerpo={"usuario":"AnaDemo", "contrasena":"ConductorDemo123!"}
            self.assertEqual(cliente.post("/acceso/conductor",base_url=base,
                headers={"Origin":"https://ajeno.invalid"},json=cuerpo).status_code,403)
            self.assertEqual(cliente.post("/acceso/conductor",base_url=base,
                headers={"Origin":base},json=cuerpo).status_code,200)
            self.assertEqual(cliente.get("/api/historial",base_url=base).status_code,200)
        with abrir_app_gunicorn(**self.opciones): pass

    def test_autoridad_privada_restringe_host_y_origen_sin_abrir_red(self):
        from datetime import datetime, timedelta, timezone
        from ipaddress import IPv4Address
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Prueba ficticia")])
        ahora = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                .public_key(clave.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("192.168.12.34"))]), critical=False)
                .sign(clave, hashes.SHA256()))
        ruta_cert = self.opciones["certificado"]
        ruta_clave = self.opciones["clave"]
        ruta_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        ruta_clave.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        opciones = self.opciones | {"direccion": "192.168.12.34", "puerto": 18771}
        with abrir_app_gunicorn(**opciones) as app:
            cliente = app.test_client()
            base = "https://192.168.12.34:18771"
            cuerpo = {"usuario": "AnaDemo", "contrasena": "ConductorDemo123!"}
            self.assertEqual(cliente.get("/api/historial", base_url=base).status_code, 401)
            self.assertEqual(cliente.get("/api/historial", base_url="https://192.168.12.35:18771").status_code, 400)
            self.assertEqual(cliente.post("/acceso/conductor", base_url=base,
                headers={"Origin": "https://ajeno.invalid"}, json=cuerpo).status_code, 403)
            self.assertEqual(cliente.post("/acceso/conductor", base_url=base,
                headers={"Origin": base}, json=cuerpo).status_code, 200)
            self.assertEqual(cliente.get("/api/historial", base_url=base).status_code, 200)

    def test_rechaza_san_ajeno_y_libera_bloqueo_tras_fallo(self):
        from ipaddress import ip_address
        with self.assertRaises(ValueError):
            with abrir_app_gunicorn(**(self.opciones | {"direccion":"192.168.1.12"})): pass
        with patch("taxiroute.servidor_gunicorn_experimental.verificar_historial_sqlite_vinculado",side_effect=ValueError):
            with self.assertRaises(ValueError):
                with abrir_app_gunicorn(**self.opciones): pass
        with abrir_app_gunicorn(**self.opciones): pass


class PruebasCicloGunicorn(unittest.TestCase):
    def test_inicializa_contexto_solo_al_cargar_worker_y_cierra_en_worker_exit(self):
        modulo=ModuleType("gunicorn.app.base")
        class BaseFicticia:
            def __init__(self):
                self.cfg=Mock();self.load_config()
        modulo.BaseApplication=BaseFicticia
        opciones={"escucha":"127.0.0.1","puerto":18772,"certificado":"cert-ficticio",
                  "clave":"clave-ficticia","aplicacion":{}}
        contexto=Mock(); app=Mock();contexto.__enter__=Mock(return_value=app);contexto.__exit__=Mock()
        with patch.dict(sys.modules,{"gunicorn":ModuleType("gunicorn"),"gunicorn.app":ModuleType("gunicorn.app"),
                                     "gunicorn.app.base":modulo}), \
             patch("taxiroute.servidor_gunicorn_experimental.abrir_app_gunicorn",return_value=contexto) as abrir:
            servidor=crear_servidor_gunicorn(opciones)
            abrir.assert_not_called()
            self.assertIs(servidor.load(),app)
            abrir.assert_called_once_with()
            with self.assertRaises(RuntimeError):servidor.load()
            servidor._salida_worker(None,None)
            contexto.__exit__.assert_called_once_with(None,None,None)
            with self.assertRaises(RuntimeError):servidor._numero_workers(None,2,1)
            valores=dict(call.args for call in servidor.cfg.set.call_args_list)
            self.assertEqual((valores["workers"],valores["worker_class"],valores["threads"]),(1,"gthread",4))
            self.assertFalse(valores["preload_app"])
            self.assertEqual(valores["secure_scheme_headers"],{})


if __name__ == "__main__":
    unittest.main()
