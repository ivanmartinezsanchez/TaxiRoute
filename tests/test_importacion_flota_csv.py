from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import csv
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from taxiroute.bloqueo import bloqueo_historial, HistorialOcupado
from taxiroute.cuentas import crear_cuenta
from taxiroute.historial import CAMPOS
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.importacion_flota_csv import importar_flota_csv, ImportacionFlotaError
from taxiroute.preparar_csv_local import preparar_archivos_csv


class PruebasImportacionFlota(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.archivos = preparar_archivos_csv(self.raiz / 'instalacion')
        self.base = self.archivos.cuentas
        self.carpeta = self.archivos.historiales
        self.ana = crear_cuenta(self.base, nombre_usuario='Ana', nombre_visible='Ana', contrasena='Conductor123!')
        self.luis = crear_cuenta(self.base, nombre_usuario='Luis', nombre_visible='Luis', contrasena='Conductor123!')
        self.destino = self.raiz / 'historial.sqlite3'
        HistorialSQLite(self.destino).inicializar()
        self.id_a, self.id_b = str(uuid4()), str(uuid4())
        self.escribir(self.ana.id, self.fila(self.id_a))
        self.escribir(self.luis.id, self.fila(self.id_b))

    def fila(self, identificador):
        return dict(zip(CAMPOS, (identificador, '2026-11-11T10:00:00+00:00',
            '2026-11-11T10:00:02+00:00', '2', '2', '0', '0.02', '0.05', '0.04')))

    def escribir(self, usuario, *filas):
        ruta = self.carpeta / usuario / 'historial.csv'
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open('w', encoding='utf-8', newline='') as f:
            escritor = csv.DictWriter(f, fieldnames=CAMPOS, lineterminator='\n')
            escritor.writeheader(); escritor.writerows(filas)
        return ruta

    def importar(self):
        return importar_flota_csv(self.base, self.carpeta, self.destino)

    def test_importa_propietarios_y_repite_sin_modificar_csv(self):
        antes = [(self.carpeta / u / 'historial.csv').read_bytes() for u in (self.ana.id, self.luis.id)]
        resultado = self.importar()
        self.assertEqual((resultado.filas_origen, resultado.insertadas, resultado.filas_sin_comprobante), (2, 2, 2))
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.ana.id).consultar()[0]['identificador'], self.id_a)
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.luis.id).consultar()[0]['identificador'], self.id_b)
        self.assertEqual((self.importar().insertadas, self.importar().ya_existentes), (0, 2))
        self.assertEqual(antes, [(self.carpeta / u / 'historial.csv').read_bytes() for u in (self.ana.id, self.luis.id)])
        with closing(sqlite3.connect(self.base)) as c, c:
            self.assertEqual(c.execute('SELECT formato, estado FROM vinculo_historial').fetchone(), ('csv', 'listo'))

    def test_importar_conserva_la_identidad_del_destino(self):
        from taxiroute.identidad_historial_sqlite import preparar_identidad_historial, leer_identidad_historial
        identidad = preparar_identidad_historial(self.destino)
        resultado = self.importar()
        self.assertEqual(resultado.insertadas, 2)
        self.assertEqual(leer_identidad_historial(self.destino), identidad)

    def test_identificador_duplicado_y_fila_invalida_no_importan_nada(self):
        self.escribir(self.luis.id, self.fila(self.id_a))
        with self.assertRaises(ImportacionFlotaError): self.importar()
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.ana.id).consultar(), [])
        fila = self.fila(self.id_b); fila['importe'] = '100.00'
        self.escribir(self.luis.id, fila)
        with self.assertRaises(ValueError): self.importar()
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.ana.id).consultar(), [])

    def test_no_ignora_directorio_de_cuenta_desconocida(self):
        (self.carpeta / str(uuid4())).mkdir()
        with self.assertRaises(ImportacionFlotaError): self.importar()

    def test_rechaza_otra_instancia_y_destino_ajeno(self):
        with bloqueo_historial(self.base.parent / 'servidor', espera=0):
            with self.assertRaises(HistorialOcupado): self.importar()
        with closing(sqlite3.connect(self.destino)) as c, c:
            c.execute('INSERT INTO carreras (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?)', (*self.fila(str(uuid4())).values(), None))
        with self.assertRaises(ImportacionFlotaError): self.importar()

    def test_rollback_si_falla_la_segunda_insercion(self):
        with closing(sqlite3.connect(self.destino)) as c, c:
            c.execute("CREATE TRIGGER falla BEFORE INSERT ON carreras WHEN (SELECT count(*) FROM carreras) = 1 BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.importar()
        with closing(sqlite3.connect(self.destino)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM carreras').fetchone()[0], 0)

    def test_trigger_ignora_fila_y_revierte_toda_la_importacion(self):
        with closing(sqlite3.connect(self.destino)) as c, c:
            c.execute("CREATE TRIGGER ignora BEFORE INSERT ON carreras WHEN (SELECT count(*) FROM carreras) = 1 BEGIN SELECT RAISE(IGNORE); END")
        with self.assertRaises(ImportacionFlotaError): self.importar()
        with closing(sqlite3.connect(self.destino)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM carreras').fetchone()[0], 0)

    def test_rechaza_referencias_huerfanas_y_subdirectorios(self):
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute("INSERT INTO auditoria_cuentas(actor_id,afectado_id,accion,fecha) VALUES (?,?,?,?)",
                      (str(uuid4()), self.ana.id, 'desactivar', '2026-11-11'))
        with self.assertRaisesRegex(ImportacionFlotaError, 'referencias'):
            self.importar()
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute('DELETE FROM auditoria_cuentas')
        (self.carpeta / self.ana.id / 'otro').mkdir()
        with self.assertRaisesRegex(ImportacionFlotaError, 'subdirectorio'):
            self.importar()

    def quitar_unicidad_destino(self):
        # Solo se altera una base temporal para simular un esquema deteriorado.
        with closing(sqlite3.connect(self.destino, isolation_level=None)) as conexion:
            sql = conexion.execute("SELECT sql FROM sqlite_schema WHERE name='carreras'").fetchone()[0]
            self.assertIn('identificador TEXT NOT NULL UNIQUE', sql)
            conexion.execute('DROP TABLE carreras')
            conexion.execute(sql.replace('identificador TEXT NOT NULL UNIQUE', 'identificador TEXT NOT NULL'))

    def test_rechaza_destino_con_identificadores_duplicados(self):
        self.quitar_unicidad_destino()
        fila = self.fila(self.id_a)
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            for _ in range(2):
                conexion.execute('INSERT INTO carreras (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?)', (*fila.values(), self.ana.id))
        with self.assertRaisesRegex(ImportacionFlotaError, 'duplicados'):
            self.importar()
        with closing(sqlite3.connect(self.destino)) as conexion:
            self.assertEqual(conexion.execute('SELECT count(*) FROM carreras').fetchone()[0], 2)

    def test_revierte_si_un_trigger_duplica_una_carrera(self):
        self.quitar_unicidad_destino()
        with closing(sqlite3.connect(self.destino)) as conexion, conexion:
            conexion.execute("""CREATE TRIGGER duplicar AFTER INSERT ON carreras
                WHEN (SELECT count(*) FROM carreras WHERE identificador=NEW.identificador)=1
                BEGIN INSERT INTO carreras (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id)
                VALUES (NEW.identificador,NEW.inicio,NEW.fin,NEW.duracion_segundos,NEW.segundos_parado,NEW.segundos_movimiento,NEW.tarifa_parado,NEW.tarifa_movimiento,NEW.importe,NEW.usuario_id); END""")
        with self.assertRaisesRegex(ImportacionFlotaError, 'no coincide'):
            self.importar()
        with closing(sqlite3.connect(self.destino)) as conexion:
            self.assertEqual(conexion.execute('SELECT count(*) FROM carreras').fetchone()[0], 0)

    def test_no_reasigna_una_carrera_con_propietario_nulo(self):
        fila = self.fila(self.id_a)
        with closing(sqlite3.connect(self.destino)) as c, c:
            c.execute('INSERT INTO carreras (identificador,inicio,fin,duracion_segundos,segundos_parado,segundos_movimiento,tarifa_parado,tarifa_movimiento,importe,usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?)', (*fila.values(), None))
        with self.assertRaises(ImportacionFlotaError): self.importar()
        with closing(sqlite3.connect(self.destino)) as c, c:
            self.assertEqual(c.execute('SELECT usuario_id FROM carreras').fetchone()[0], None)

    def test_rechaza_carrera_pendiente_en_base(self):
        from secrets import token_bytes
        from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute('UPDATE usuarios SET cambio_obligatorio=0')
        args = dict(ruta_cuentas=self.base, ruta_tarifas=self.archivos.tarifas,
                    carpeta_historial=self.carpeta, clave_hmac=token_bytes(32),
                    enviar_pin=lambda *_: None, durable=True)
        with abrir_app_csv_experimental(**args) as app:
            cliente = app.test_client(); url = 'https://localhost'
            headers = {'Origin': url}
            acceso = cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'})
            self.assertEqual(acceso.status_code, 200)
            inicio = cliente.post('/api/carrera/iniciar', base_url=url, headers=headers,
                json={'revision': 0, 'id_operacion': str(uuid4())})
            self.assertEqual(inicio.status_code, 200)
        with self.assertRaises(ImportacionFlotaError): self.importar()
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.ana.id).consultar(), [])

    def test_rechaza_propietario_huerfano_sin_definicion_fk(self):
        from secrets import token_bytes
        from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute('UPDATE usuarios SET cambio_obligatorio=0')
        args = dict(ruta_cuentas=self.base, ruta_tarifas=self.archivos.tarifas,
                    carpeta_historial=self.carpeta, clave_hmac=token_bytes(32),
                    enviar_pin=lambda *_: None, durable=True)
        with abrir_app_csv_experimental(**args) as app:
            cliente = app.test_client(); url = 'https://localhost'; headers = {'Origin': url}
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/iniciar', base_url=url, headers=headers,
                json={'revision': 0, 'id_operacion': str(uuid4())}).status_code, 200)
        # Solo esta base temporal se altera para reproducir una FK ausente.
        with closing(sqlite3.connect(self.base, isolation_level=None)) as c:
            sql = c.execute("SELECT sql FROM sqlite_schema WHERE name='carreras_recuperacion'").fetchone()[0]
            self.assertIn('REFERENCES usuarios(id)', sql)
            c.execute('PRAGMA writable_schema=ON')
            c.execute("UPDATE sqlite_schema SET sql=? WHERE name='carreras_recuperacion'",
                      (sql.replace('REFERENCES usuarios(id)', ''),))
            version = c.execute('PRAGMA schema_version').fetchone()[0]
            c.execute(f'PRAGMA schema_version={version + 1}')
            c.execute('PRAGMA writable_schema=OFF')
        with closing(sqlite3.connect(self.base)) as c, c:
            self.assertFalse(c.execute('PRAGMA foreign_key_list(carreras_recuperacion)').fetchall())
            c.execute('UPDATE carreras_recuperacion SET usuario_id=?', (str(uuid4()),))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
        with self.assertRaisesRegex(ImportacionFlotaError, 'referencias'):
            self.importar()
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.ana.id).consultar(), [])

    def test_rechaza_csv_ausente_con_cierre_confirmado(self):
        from secrets import token_bytes
        from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute('UPDATE usuarios SET cambio_obligatorio=0')
        args = dict(ruta_cuentas=self.base, ruta_tarifas=self.archivos.tarifas,
                    carpeta_historial=self.carpeta, clave_hmac=token_bytes(32),
                    enviar_pin=lambda *_: None, durable=True)
        with abrir_app_csv_experimental(**args) as app:
            cliente = app.test_client(); url = 'https://localhost'; headers = {'Origin': url}
            self.assertEqual(cliente.post('/acceso/conductor', base_url=url, headers=headers,
                json={'usuario': 'Ana', 'contrasena': 'Conductor123!'}).status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/iniciar', base_url=url, headers=headers,
                json={'revision': 0, 'id_operacion': str(uuid4())}).status_code, 200)
            self.assertEqual(cliente.post('/api/carrera/finalizar', base_url=url, headers=headers,
                json={'revision': 1}).status_code, 200)
        (self.carpeta / self.ana.id / 'historial.csv').unlink()
        with self.assertRaises(ImportacionFlotaError): self.importar()
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=self.luis.id).consultar(), [])

    def test_indica_historial_ausente_y_legado_sin_asignar(self):
        (self.carpeta / self.ana.id / 'historial.csv').unlink()
        (self.carpeta / 'historial.csv').write_text('legado', encoding='utf-8')
        resultado = self.importar()
        self.assertEqual((resultado.cuentas_sin_historial, resultado.legado_sin_propietario), (1, True))
        self.assertEqual(HistorialSQLite(self.destino, usuario_id=None).consultar(), [])

if __name__ == '__main__':
    unittest.main()
