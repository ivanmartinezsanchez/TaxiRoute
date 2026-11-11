"""Pruebas aisladas de la demo SQLite vinculada, sin credenciales reales."""
from contextlib import closing
from pathlib import Path
import sqlite3
import ssl
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.request import HTTPSHandler, build_opener
from uuid import uuid4
from unittest.mock import patch

from werkzeug.serving import make_server

from taxiroute.bloqueo import HistorialOcupado
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO
from taxiroute.demo_sqlite_contenedor import abrir_demo_sqlite, preparar_demo_sqlite


class PruebasDemoSQLiteContenedor(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.demo = self.raiz / 'demo'
        self.codigos = []
        self.url = 'https://127.0.0.1'
        self.headers = {'Origin': self.url}

    def abrir(self):
        return abrir_demo_sqlite(self.demo, mostrar_codigo=self.codigos.append)

    def post(self, cliente, ruta, datos):
        return cliente.post(ruta, base_url=self.url, headers=self.headers, json=datos)

    def entrar(self, app):
        cliente = app.test_client()
        self.assertEqual(self.post(cliente, '/acceso/conductor',
            {'usuario':'AnaDemo', 'contrasena':CLAVE_CONDUCTOR_DEMO}).status_code, 200)
        return cliente

    def test_accesos_pin_ficticio_y_carrera_persistente(self):
        with patch('taxiroute.oauth_gmail.cliente_gmail_autorizado', side_effect=AssertionError('No usar Gmail')):
            with self.abrir() as app:
                admin = app.test_client()
                self.assertEqual(self.post(admin, '/acceso/administrador',
                    {'usuario':'AdminDemo', 'contrasena':CLAVE_ADMIN_DEMO}).json['paso'], 'pin_pendiente')
                self.assertEqual(self.post(admin, '/acceso/administrador/pin', {'codigo':self.codigos[-1]}).json['paso'], 'acceso_completado')
                self.assertIn('No se envía ningún correo', admin.get('/acceso', base_url=self.url).get_data(as_text=True))
                cliente = self.entrar(app)
                inicio = self.post(cliente, '/api/carrera/iniciar', {'revision':0, 'id_operacion':str(uuid4())})
                self.assertEqual(inicio.status_code, 200)
                self.assertEqual(self.post(cliente, '/api/carrera/finalizar', {'revision':inicio.json['revision']}).status_code, 200)
            with self.abrir() as app:
                self.assertEqual(len(self.entrar(app).get('/api/historial', base_url=self.url).json['carreras']), 1)
        with closing(sqlite3.connect(self.demo / 'cuentas.sqlite3')) as conexion:
            self.assertEqual(conexion.execute('SELECT formato, estado FROM vinculo_historial').fetchone(), ('sqlite', 'listo'))
            self.assertEqual(conexion.execute('SELECT count(*) FROM procedencia_sqlite').fetchone()[0], 1)

    def test_https_local_y_exclusion_segunda_instancia(self):
        certificado, clave = certificado_temporal(self.raiz)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificado, clave)
        with self.abrir() as app:
            with self.assertRaises(HistorialOcupado):
                with self.abrir():
                    self.fail('Una segunda instancia no debe abrir el volumen')
            servidor = make_server('127.0.0.1', 0, app, ssl_context=tls, threaded=True)
            hilo = Thread(target=servidor.serve_forever, daemon=True)
            hilo.start()
            try:
                cliente = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=str(certificado))))
                with cliente.open(f'https://127.0.0.1:{servidor.server_port}/acceso', timeout=5) as respuesta:
                    self.assertEqual(respuesta.status, 200)
            finally:
                servidor.shutdown()
                hilo.join(timeout=5)
                servidor.server_close()
                self.assertFalse(hilo.is_alive())

    def test_rechaza_carpeta_ajena_y_no_modifica_su_contenido(self):
        self.demo.mkdir()
        archivo = self.demo / 'conservar.txt'
        archivo.write_text('conservar', encoding='utf-8')
        with self.assertRaises(ValueError):
            preparar_demo_sqlite(self.demo)
        self.assertEqual(archivo.read_text(encoding='utf-8'), 'conservar')
        self.assertEqual([p.name for p in self.demo.iterdir()], ['conservar.txt'])

    def test_rechaza_cambio_de_correo_y_no_envia(self):
        preparar_demo_sqlite(self.demo)
        with closing(sqlite3.connect(self.demo / 'cuentas.sqlite3')) as conexion, conexion:
            conexion.execute("UPDATE configuracion_administrador SET correo_verificacion='otro@example.invalid'")
        with self.assertRaises(ValueError):
            with self.abrir():
                self.fail('No debe aceptar otro buzón')
        self.assertEqual(self.codigos, [])

    def test_rechaza_cambio_de_identidad(self):
        preparar_demo_sqlite(self.demo)
        with closing(sqlite3.connect(self.demo / 'historial.sqlite3')) as conexion, conexion:
            conexion.execute('UPDATE identidad_historial SET identificador=?', (str(uuid4()),))
        with self.assertRaises(ValueError):
            with self.abrir():
                self.fail('No debe abrir una identidad distinta')


if __name__ == '__main__':
    unittest.main()
