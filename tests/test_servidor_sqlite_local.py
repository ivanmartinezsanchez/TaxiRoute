"""Pruebas HTTPS locales de SQLite, siempre con datos ficticios."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
import csv
from http.cookiejar import CookieJar
import json
from pathlib import Path
import sqlite3
import ssl
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.bloqueo import HistorialOcupado
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.historial import CAMPOS
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.importacion_flota_csv import activar_flota_sqlite
from taxiroute.inicializacion_administrador import preparar_primer_administrador
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.servidor_sqlite_local import servidor_sqlite_experimental


class PruebasServidorSQLite(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / 'datos')
        self.certificado, self.clave = certificado_temporal(self.raiz)
        admin = preparar_primer_administrador(self.archivos.cuentas, nombre_usuario='Admin',
            nombre_visible='Admin de prueba', contrasena='AdminPrueba123!', correo='demo@example.invalid')
        conductor = crear_cuenta(self.archivos.cuentas, nombre_usuario='Ana', nombre_visible='Ana de prueba',
                                 contrasena='ConductorPrueba123!')
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute('UPDATE usuarios SET cambio_obligatorio=0 WHERE id IN (?, ?)', (admin.id, conductor.id))
        for usuario in (admin.id, conductor.id):
            archivo = self.archivos.historiales / usuario / 'historial.csv'
            archivo.parent.mkdir(parents=True, exist_ok=True)
            with archivo.open('w', encoding='utf-8', newline='') as f:
                csv.DictWriter(f, fieldnames=CAMPOS).writeheader()
        self.historial = self.raiz / 'historial.sqlite3'
        HistorialSQLite(self.historial).inicializar()
        activar_flota_sqlite(self.archivos.cuentas, self.archivos.historiales, self.historial)
        self.buzon = []
        self.args = dict(ruta_cuentas=self.archivos.cuentas, ruta_historial=self.historial,
                         ruta_tarifas=self.archivos.tarifas, certificado=self.certificado,
                         clave=self.clave, puerto=0, enviar_pin=lambda correo, codigo: self.buzon.append((correo, codigo)))

    def test_https_acceso_carrera_y_persistencia_tras_reabrir(self):
        def recorrer(servidor, primera):
            self.assertEqual(servidor.server_address[0], '127.0.0.1')
            hilo = Thread(target=servidor.serve_forever, daemon=True)
            hilo.start()
            try:
                base = f'https://127.0.0.1:{servidor.server_port}'
                tls = ssl.create_default_context(cafile=str(self.certificado))
                conductor = build_opener(HTTPSHandler(context=tls), HTTPCookieProcessor(CookieJar()))
                admin = build_opener(HTTPSHandler(context=tls), HTTPCookieProcessor(CookieJar()))
                anonimo = build_opener(HTTPSHandler(context=tls), HTTPCookieProcessor(CookieJar()))
                def peticion(cliente, ruta, datos=None):
                    cuerpo = None if datos is None else json.dumps(datos).encode('utf-8')
                    headers = {} if datos is None else {'Origin': base, 'Content-Type': 'application/json'}
                    with cliente.open(Request(base + ruta, data=cuerpo, headers=headers), timeout=5) as respuesta:
                        return json.load(respuesta)
                with self.assertRaises(HTTPError) as denegada:
                    peticion(anonimo, '/api/historial')
                self.assertEqual(denegada.exception.code, 401)
                peticion(conductor, '/acceso/conductor', {'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
                if primera:
                    respuesta = peticion(admin, '/acceso/administrador', {'usuario': 'Admin', 'contrasena': 'AdminPrueba123!'})
                    self.assertEqual(respuesta['paso'], 'pin_pendiente')
                    self.assertEqual(self.buzon[-1][0], 'demo@example.invalid')
                    self.assertEqual(peticion(admin, '/acceso/administrador/pin', {'codigo': self.buzon[-1][1]})['paso'], 'acceso_completado')
                    revision = peticion(conductor, '/api/carrera')['revision']
                    inicio = peticion(conductor, '/api/carrera/iniciar', {'revision': revision, 'id_operacion': str(uuid4())})
                    fin = peticion(conductor, '/api/carrera/finalizar', {'revision': inicio['revision']})
                    self.assertFalse(fin['activa'])
                self.assertEqual(len(peticion(conductor, '/api/historial')['carreras']), 1)
            finally:
                servidor.shutdown()
                hilo.join(timeout=5)
                self.assertFalse(hilo.is_alive())
        with servidor_sqlite_experimental(**self.args) as servidor:
            recorrer(servidor, True)
        with servidor_sqlite_experimental(**self.args) as servidor:
            recorrer(servidor, False)

    def test_fallo_del_transporte_no_omite_el_pin(self):
        def fallar(_correo, _codigo):
            raise OSError('fallo ficticio de transporte')
        with servidor_sqlite_experimental(**(self.args | {'enviar_pin': fallar})) as servidor:
            admin = servidor.app.test_client()
            conductor = servidor.app.test_client()
            url, headers = 'https://localhost', {'Origin': 'https://localhost'}
            fallo = admin.post('/acceso/administrador', base_url=url, headers=headers,
                json={'usuario': 'Admin', 'contrasena': 'AdminPrueba123!'})
            self.assertEqual(fallo.status_code, 401)
            self.assertIsNone(admin.get_cookie('taxiroute_sesion'))
            acceso = conductor.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
            self.assertEqual(acceso.status_code, 200)

    def certificado_para_ip(self, direccion):
        clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Prueba ficticia')])
        ahora = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                .public_key(clave.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(ahora - timedelta(minutes=1)).not_valid_after(ahora + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address(direccion))]), critical=False)
                .sign(clave, hashes.SHA256()))
        cert_path = self.raiz / 'red-cert.pem'
        key_path = self.raiz / 'red-key.pem'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return cert_path, key_path

    def test_rechaza_direcciones_no_privadas_sin_abrir_puerto(self):
        for direccion in ('0.0.0.0', '127.0.0.2', '8.8.8.8', '100.64.0.1', '172.15.0.1', '172.32.0.1', 'localhost', '::1'):
            with self.subTest(direccion=direccion), patch('taxiroute.servidor_sqlite_local.make_server') as crear:
                with self.assertRaises(ValueError):
                    with servidor_sqlite_experimental(**(self.args | {'direccion': direccion, 'puerto': 8769})):
                        pass
                crear.assert_not_called()

    def test_direcciones_privadas_admitidas_y_local_por_defecto(self):
        from taxiroute.servidor_sqlite_local import _validar_direccion
        for direccion in ('127.0.0.1', '10.1.2.3', '172.16.0.1', '172.31.255.254', '192.168.1.3'):
            with self.subTest(direccion=direccion):
                self.assertEqual(str(_validar_direccion(direccion)), direccion)

    def test_red_rechaza_san_distinto_y_puerto_automatico(self):
        with patch('taxiroute.servidor_sqlite_local.make_server') as crear:
            with self.assertRaisesRegex(ValueError, 'certificado'):
                with servidor_sqlite_experimental(**(self.args | {'direccion': '192.168.12.34', 'puerto': 8769})):
                    pass
            with self.assertRaisesRegex(ValueError, 'puerto'):
                with servidor_sqlite_experimental(**(self.args | {'direccion': '192.168.12.34'})):
                    pass
            crear.assert_not_called()

    def test_red_acepta_solo_host_y_puerto_configurados_sin_escuchar_en_red(self):
        cert, clave = self.certificado_para_ip('192.168.12.34')
        with patch('taxiroute.servidor_sqlite_local.make_server') as crear:
            opciones = self.args | {'direccion': '192.168.12.34', 'puerto': 8769,
                                    'certificado': cert, 'clave': clave}
            with servidor_sqlite_experimental(**opciones):
                argumentos = crear.call_args.args
                self.assertEqual(argumentos[:2], ('192.168.12.34', 8769))
                cliente = argumentos[2].test_client()
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.34:8769').status_code, 401)
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.34:8770').status_code, 400)
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.35:8769').status_code, 400)
                respuesta = cliente.post('/acceso/conductor', base_url='https://192.168.12.34:8769',
                    headers={'Origin': 'https://192.168.12.35:8769'}, json={'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
                self.assertEqual(respuesta.status_code, 403)
                respuesta = cliente.get('/api/historial', base_url='https://192.168.12.34:8769',
                                       headers={'X-Forwarded-Host': 'otro.ejemplo'})
                self.assertEqual(respuesta.status_code, 401)
                acceso = cliente.post('/acceso/conductor', base_url='https://192.168.12.34:8769',
                    headers={'Origin': 'https://192.168.12.34:8769'},
                    json={'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
                self.assertEqual(acceso.status_code, 200)
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.34:8769').status_code, 200)

    def test_contenedor_separa_escucha_y_autoridad_sin_abrir_red(self):
        cert, clave = self.certificado_para_ip('192.168.12.34')
        opciones = self.args | {'direccion': '192.168.12.34', 'puerto': 8769,
                                'certificado': cert, 'clave': clave, 'escucha_contenedor': True}
        with patch('taxiroute.servidor_sqlite_local.make_server') as crear:
            with servidor_sqlite_experimental(**opciones):
                argumentos = crear.call_args.args
                self.assertEqual(argumentos[:2], ('0.0.0.0', 8769))
                cliente = argumentos[2].test_client()
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.34:8769').status_code, 401)
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.35:8769').status_code, 400)
                self.assertEqual(cliente.get('/api/historial', base_url='https://192.168.12.34:8770').status_code, 400)
                acceso = cliente.post('/acceso/conductor', base_url='https://192.168.12.34:8769',
                    headers={'Origin': 'https://192.168.12.35:8769'},
                    json={'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
                self.assertEqual(acceso.status_code, 403)

    def test_contenedor_local_solo_admite_autoridad_configurada(self):
        opciones = self.args | {'puerto': 18770, 'escucha_contenedor': True}
        with patch('taxiroute.servidor_sqlite_local.make_server') as crear:
            with servidor_sqlite_experimental(**opciones):
                argumentos = crear.call_args.args
                self.assertEqual(argumentos[:2], ('0.0.0.0', 18770))
                cliente = argumentos[2].test_client()
                self.assertEqual(cliente.get('/api/historial', base_url='https://127.0.0.1:18770').status_code, 401)
                self.assertEqual(cliente.get('/api/historial', base_url='https://127.0.0.1:18771').status_code, 400)
                self.assertEqual(cliente.get('/api/historial', base_url='https://localhost:18770').status_code, 400)
                respuesta = cliente.post('/acceso/conductor', base_url='https://127.0.0.1:18770',
                    headers={'Origin': 'https://ejemplo.invalid'},
                    json={'usuario': 'Ana', 'contrasena': 'ConductorPrueba123!'})
                self.assertEqual(respuesta.status_code, 403)

    def test_contenedor_local_rechaza_certificado_sin_ip_local(self):
        cert, clave = self.certificado_para_ip('192.168.12.34')
        opciones = self.args | {'puerto': 18770, 'escucha_contenedor': True,
                                'certificado': cert, 'clave': clave}
        with patch('taxiroute.servidor_sqlite_local.make_server') as crear:
            with self.assertRaisesRegex(ValueError, 'certificado'):
                with servidor_sqlite_experimental(**opciones):
                    pass
            crear.assert_not_called()

    def test_contenedor_rechaza_puerto_automatico_y_valores_no_booleanos(self):
        for opciones in ({'escucha_contenedor': True}, {'escucha_contenedor': 1},
                         {'escucha_contenedor': 'true'}):
            with self.subTest(opciones=opciones), patch('taxiroute.servidor_sqlite_local.make_server') as crear:
                with self.assertRaises(ValueError):
                    with servidor_sqlite_experimental(**(self.args | opciones)):
                        pass
                crear.assert_not_called()

    def test_excluye_segunda_instancia(self):
        with servidor_sqlite_experimental(**self.args):
            with self.assertRaises(HistorialOcupado):
                with servidor_sqlite_experimental(**self.args):
                    pass

    def test_no_abre_tls_invalido_o_base_sin_vinculo(self):
        with patch('taxiroute.servidor_sqlite_local.make_server') as servidor:
            with self.assertRaises((OSError, ValueError)):
                with servidor_sqlite_experimental(**(self.args | {'certificado': self.clave})):
                    pass
            servidor.assert_not_called()
        otra = self.raiz / 'sin-vinculo.sqlite3'
        inicializar_base(otra)
        with patch('taxiroute.servidor_sqlite_local.make_server') as servidor:
            with self.assertRaises(ValueError):
                with servidor_sqlite_experimental(**(self.args | {'ruta_cuentas': otra})):
                    pass
            servidor.assert_not_called()

    def test_no_abre_historial_ajeno_ni_cuentas_sin_admin(self):
        ajeno = self.raiz / 'ajeno.sqlite3'
        HistorialSQLite(ajeno).inicializar()
        with patch('taxiroute.servidor_sqlite_local.make_server') as servidor:
            with self.assertRaises(ValueError):
                with servidor_sqlite_experimental(**(self.args | {'ruta_historial': ajeno})):
                    pass
            servidor.assert_not_called()
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=0 WHERE rol='administrador'")
        with patch('taxiroute.servidor_sqlite_local.make_server') as servidor:
            with self.assertRaisesRegex(ValueError, 'administrador'):
                with servidor_sqlite_experimental(**self.args):
                    pass
            servidor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
