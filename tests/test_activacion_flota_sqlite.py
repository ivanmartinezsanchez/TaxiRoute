from contextlib import closing
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
import csv
import sqlite3

from taxiroute.bloqueo import HistorialOcupado
from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.cuentas import crear_cuenta
from taxiroute.historial import CAMPOS
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.identidad_historial_sqlite import leer_identidad_historial
from taxiroute.importacion_flota_csv import activar_flota_sqlite, ImportacionFlotaError
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.vinculo_historial import verificar_vinculo_csv


class PruebasActivacionFlota(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / 'instalacion')
        self.base = self.archivos.cuentas
        self.carpeta = self.archivos.historiales
        self.destino = self.raiz / 'historial.sqlite3'
        HistorialSQLite(self.destino).inicializar()
        self.ana = crear_cuenta(self.base, nombre_usuario='Ana', nombre_visible='Ana', contrasena='Conductor123!')
        self.luis = crear_cuenta(self.base, nombre_usuario='Luis', nombre_visible='Luis', contrasena='Conductor123!')
        with closing(sqlite3.connect(self.base)) as conexion, conexion:
            conexion.execute('UPDATE usuarios SET cambio_obligatorio=0')
        self.opciones = dict(ruta_cuentas=self.base, ruta_tarifas=self.archivos.tarifas,
            carpeta_historial=self.carpeta, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None, durable=True)
        with abrir_app_csv_experimental(**self.opciones) as app:
            for nombre in ('Ana', 'Luis'):
                cliente = app.test_client()
                url = 'https://localhost'
                origen = {'Origin': url}
                respuesta = cliente.post('/acceso/conductor', base_url=url, headers=origen,
                    json={'usuario': nombre, 'contrasena': 'Conductor123!'})
                self.assertEqual(respuesta.status_code, 200)
                inicio = cliente.post('/api/carrera/iniciar', base_url=url, headers=origen,
                    json={'revision': 0, 'id_operacion': str(uuid4())})
                self.assertEqual(inicio.status_code, 200)
                fin = cliente.post('/api/carrera/finalizar', base_url=url, headers=origen,
                    json={'revision': 1})
                self.assertEqual(fin.status_code, 200)
        self.originales = {u: (self.carpeta / u / 'historial.csv').read_bytes()
                           for u in (self.ana.id, self.luis.id)}

    def vinculo(self):
        with closing(sqlite3.connect(self.base)) as conexion:
            return conexion.execute('SELECT formato, identificador, estado FROM vinculo_historial').fetchone()

    def activar(self):
        return activar_flota_sqlite(self.base, self.carpeta, self.destino)

    def test_activa_con_dos_conductores_y_conserva_csv(self):
        anterior = self.vinculo()
        resultado = self.activar()
        self.assertEqual((resultado.cuentas, resultado.filas_origen, resultado.insertadas), (2, 2, 2))
        self.assertEqual(self.vinculo(), ('sqlite', resultado.identidad_sqlite, 'listo'))
        self.assertNotEqual(resultado.identidad_sqlite, anterior[1])
        self.assertEqual(leer_identidad_historial(self.destino), resultado.identidad_sqlite)
        for usuario, original in self.originales.items():
            self.assertEqual((self.carpeta / usuario / 'historial.csv').read_bytes(), original)
            self.assertEqual(len(HistorialSQLite(self.destino, usuario_id=usuario).consultar()), 1)
        with self.assertRaises(ValueError):
            verificar_vinculo_csv(self.base, self.carpeta)

    def test_activacion_abre_web_y_conserva_historial_tras_reiniciar(self):
        from taxiroute.composicion_experimental import abrir_app_experimental
        self.activar()
        opciones = dict(ruta_cuentas=self.base, ruta_historial=self.destino,
                        ruta_tarifas=self.archivos.tarifas, clave_hmac=token_bytes(32),
                        enviar_pin=lambda *_: None)
        url, origen = 'https://localhost', {'Origin': 'https://localhost'}
        with abrir_app_experimental(**opciones) as app:
            cliente = app.test_client()
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=origen,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(len(cliente.get('/api/historial', base_url=url).json['carreras']), 1)
            revision = cliente.get('/api/carrera', base_url=url).json['revision']
            inicio = cliente.post('/api/carrera/iniciar', base_url=url, headers=origen,
                json={'revision': revision, 'id_operacion': str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/finalizar', base_url=url, headers=origen,
                json={'revision': inicio.json['revision']}).status_code, 200)
        with abrir_app_experimental(**opciones) as app:
            cliente = app.test_client()
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=origen,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(len(cliente.get('/api/historial', base_url=url).json['carreras']), 2)
        for usuario, original in self.originales.items():
            self.assertEqual((self.carpeta / usuario / 'historial.csv').read_bytes(), original)

    def test_fallo_antes_de_cambiar_vinculo_conserva_csv_y_permite_reintentar(self):
        anterior = self.vinculo()
        with patch('taxiroute.importacion_flota_csv._cambiar_vinculo', side_effect=OSError('corte ficticio')):
            with self.assertRaises(OSError):
                self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertTrue(verificar_vinculo_csv(self.base, self.carpeta))
        identidad = leer_identidad_historial(self.destino)
        self.assertIsNotNone(identidad)
        resultado = self.activar()
        self.assertEqual((resultado.insertadas, resultado.ya_existentes), (0, 2))
        self.assertEqual(resultado.identidad_sqlite, identidad)
        self.assertEqual(self.vinculo(), ('sqlite', identidad, 'listo'))

    def test_fallo_despues_de_update_revierte_cuentas_y_permanece_reintentable(self):
        from taxiroute.importacion_flota_csv import _cambiar_vinculo
        anterior = self.vinculo()
        def corte(conexion, origen, destino):
            _cambiar_vinculo(conexion, origen, destino)
            raise OSError('corte antes del commit')
        with patch('taxiroute.importacion_flota_csv._cambiar_vinculo', side_effect=corte):
            with self.assertRaises(OSError):
                self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertTrue(verificar_vinculo_csv(self.base, self.carpeta))
        self.assertEqual(self.activar().ya_existentes, 2)

    def test_no_activa_con_servidor_en_marcha(self):
        anterior = self.vinculo()
        with abrir_app_csv_experimental(**self.opciones):
            with self.assertRaises(HistorialOcupado):
                self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertIsNone(leer_identidad_historial(self.destino))

    def test_rechaza_historial_sin_propietario(self):
        (self.carpeta / 'historial.csv').write_text('legado', encoding='utf-8')
        anterior = self.vinculo()
        with self.assertRaisesRegex(ImportacionFlotaError, 'revisar'):
            self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertIsNone(leer_identidad_historial(self.destino))

    def test_rechaza_cuenta_sin_csv(self):
        crear_cuenta(self.base, nombre_usuario='Sara', nombre_visible='Sara', contrasena='Conductor123!')
        anterior = self.vinculo()
        with self.assertRaisesRegex(ImportacionFlotaError, 'revisar'):
            self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertIsNone(leer_identidad_historial(self.destino))

    def test_rechaza_fila_sin_comprobante(self):
        ruta = self.carpeta / self.ana.id / 'historial.csv'
        with ruta.open(encoding='utf-8', newline='') as f:
            original = next(csv.DictReader(f))
        original['identificador'] = str(uuid4())
        with ruta.open('a', encoding='utf-8', newline='') as f:
            csv.DictWriter(f, fieldnames=CAMPOS, lineterminator='\n').writerow(original)
        anterior = self.vinculo()
        with self.assertRaisesRegex(ImportacionFlotaError, 'revisar'):
            self.activar()
        self.assertEqual(self.vinculo(), anterior)
        self.assertIsNone(leer_identidad_historial(self.destino))
