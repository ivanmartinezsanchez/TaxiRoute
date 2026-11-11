from contextlib import closing
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.cuentas import crear_cuenta
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.identidad_historial_sqlite import preparar_identidad_historial
from taxiroute.importacion_flota_csv import importar_flota_csv
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado, VerificacionHistorialSQLiteError


class PruebasVerificacionSQLite(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / 'instalacion')
        self.usuario = crear_cuenta(self.archivos.cuentas, nombre_usuario='Ana', nombre_visible='Ana', contrasena='Conductor123!')
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion, conexion:
            conexion.execute('UPDATE usuarios SET cambio_obligatorio=0')
        with abrir_app_csv_experimental(ruta_cuentas=self.archivos.cuentas, ruta_tarifas=self.archivos.tarifas,
                carpeta_historial=self.archivos.historiales, clave_hmac=token_bytes(32),
                enviar_pin=lambda *_: None, durable=True) as app:
            cliente = app.test_client()
            url = 'https://localhost'
            headers = {'Origin': url}
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/iniciar', base_url=url, headers=headers,
                json={'revision': 0, 'id_operacion': str(uuid4())}).status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/finalizar', base_url=url, headers=headers,
                json={'revision': 1}).status_code, 200)
        self.destino = self.raiz / 'historial.sqlite3'
        HistorialSQLite(self.destino).inicializar()
        importar_flota_csv(self.archivos.cuentas, self.archivos.historiales, self.destino)
        self.identidad = preparar_identidad_historial(self.destino)
        # Solo en esta instalación temporal se simula el vínculo ya conmutado.
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion, conexion:
            conexion.execute("UPDATE vinculo_historial SET formato='sqlite', identificador=?, estado='listo'", (self.identidad,))

    def comprobar(self):
        verificar_historial_sqlite_vinculado(self.archivos.cuentas, self.destino)

    def test_acepta_identidad_y_cierre_coherentes(self):
        self.comprobar()
        self.assertEqual(len(HistorialSQLite(self.destino, usuario_id=self.usuario.id).consultar()), 1)

    def test_rechaza_identidad_distinta_y_cierre_ausente(self):
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute('UPDATE identidad_historial SET identificador=?', (str(uuid4()),))
        with self.assertRaisesRegex(VerificacionHistorialSQLiteError, 'identidad'):
            self.comprobar()
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute('UPDATE identidad_historial SET identificador=?', (self.identidad,))
            conexion.execute('DELETE FROM carreras')
        with self.assertRaisesRegex(VerificacionHistorialSQLiteError, 'cierre confirmado'):
            self.comprobar()

    def test_rechaza_propietario_alterado_y_datos_modificados(self):
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute('UPDATE carreras SET usuario_id=NULL')
        with self.assertRaisesRegex(VerificacionHistorialSQLiteError, 'cierre confirmado'):
            self.comprobar()
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute('UPDATE carreras SET usuario_id=?, importe=?', (self.usuario.id, '999.00'))
        with self.assertRaises(ValueError):
            self.comprobar()

    def test_app_abre_historial_vinculado_y_conserva_carreras_al_reiniciar(self):
        from taxiroute.composicion_experimental import abrir_app_experimental
        args = dict(ruta_cuentas=self.archivos.cuentas, ruta_tarifas=self.archivos.tarifas,
                    ruta_historial=self.destino, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None)
        url = 'https://localhost'
        headers = {'Origin': url}
        with abrir_app_experimental(**args) as app:
            cliente = app.test_client()
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(len(cliente.get('/api/historial', base_url=url).json['carreras']), 1)
            revision = cliente.get('/api/carrera', base_url=url).json['revision']
            inicio = cliente.post('/api/carrera/iniciar', base_url=url, headers=headers,
                json={'revision': revision, 'id_operacion': str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/finalizar', base_url=url, headers=headers,
                json={'revision': inicio.json['revision']}).status_code, 200)
        with abrir_app_experimental(**args) as app:
            cliente = app.test_client()
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(len(cliente.get('/api/historial', base_url=url).json['carreras']), 2)

    def test_app_no_arranca_si_falta_un_cierre_confirmado(self):
        from taxiroute.composicion_experimental import abrir_app_experimental
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute('DELETE FROM carreras')
        with self.assertRaises(VerificacionHistorialSQLiteError):
            with abrir_app_experimental(ruta_cuentas=self.archivos.cuentas, ruta_tarifas=self.archivos.tarifas,
                    ruta_historial=self.destino, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None):
                self.fail('No debe arrancar con un cierre ausente')

    def test_rechaza_vinculo_csv_o_archivo_ausente(self):
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion, conexion:
            conexion.execute("UPDATE vinculo_historial SET formato='csv'")
        with self.assertRaisesRegex(VerificacionHistorialSQLiteError, 'no está vinculado'):
            self.comprobar()
        with self.assertRaises(VerificacionHistorialSQLiteError):
            verificar_historial_sqlite_vinculado(self.archivos.cuentas, self.raiz / 'ausente.sqlite3')


if __name__ == '__main__':
    unittest.main()
