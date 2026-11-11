from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import sqlite3
import unittest
from unittest.mock import patch

from taxiroute.asistente_migracion_local import main
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cuentas import crear_cuenta
from taxiroute.historial import CAMPOS
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.vinculo_historial import verificar_vinculo_csv


class PruebasAsistenteMigracion(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(self.raiz / 'instalacion')
        self.admin = crear_cuenta(self.archivos.cuentas, nombre_usuario='Admin', nombre_visible='Admin',
                                  contrasena='Conductor123!', rol='administrador')
        self.ana = crear_cuenta(self.archivos.cuentas, nombre_usuario='Ana', nombre_visible='Ana',
                                contrasena='Conductor123!')
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion, conexion:
            conexion.execute('UPDATE usuarios SET cambio_obligatorio=0')
            conexion.execute('INSERT INTO configuracion_administrador VALUES (?, ?)', (self.admin.id, 'demo@example.invalid'))
        self.destino = self.raiz / 'historial.sqlite3'
        self.args = ['--cuentas', str(self.archivos.cuentas), '--historiales', str(self.archivos.historiales), '--destino', str(self.destino)]
        self.buzon, self.salida = [], []

    def ejecutar(self, respuestas, *, leer=None):
        respuestas = iter(respuestas)
        preguntas_secretas = iter(['Conductor123!', None])
        def secreto(_):
            valor = next(preguntas_secretas)
            return self.buzon[-1] if valor is None else valor
        return main(self.args, leer=leer or (lambda _: next(respuestas)), secreto=secreto,
                    enviar_pin=lambda _, codigo: self.buzon.append(codigo),
                    escribir=self.salida.append, interactiva=True)

    def test_revision_individual_y_activacion_con_autenticacion(self):
        self.assertEqual(self.ejecutar(['Admin', 'REVISADO', 'REVISADO', 'MIGRAR']), 0)
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion:
            self.assertEqual(conexion.execute('SELECT formato FROM vinculo_historial').fetchone()[0], 'sqlite')
            revisiones = conexion.execute('SELECT usuario_id, actor_id FROM revisiones_csv_ausente').fetchall()
            self.assertEqual({x[0] for x in revisiones}, {self.admin.id, self.ana.id})
            self.assertTrue(all(x[1] == self.admin.id for x in revisiones))
            self.assertEqual(conexion.execute('SELECT count(*) FROM sesiones WHERE revocada_en IS NULL').fetchone()[0], 0)
        self.assertTrue(self.destino.is_file())
        self.assertTrue(any('CSV se conservan' in s for s in self.salida))

    def test_cancelar_no_crea_destino_ni_cambia_vinculo(self):
        self.assertEqual(self.ejecutar(['Admin', 'NO']), 1)
        self.assertFalse(self.destino.exists())
        self.assertTrue(verificar_vinculo_csv(self.archivos.cuentas, self.archivos.historiales))

    def test_no_interactiva_y_servidor_activo_no_envian_codigo(self):
        self.assertEqual(main(self.args, escribir=self.salida.append, interactiva=False), 2)
        with bloqueo_historial(self.archivos.cuentas.parent / 'servidor', espera=0):
            self.assertEqual(self.ejecutar([]), 1)
        self.assertEqual(self.buzon, [])
        self.assertFalse(self.destino.exists())

    def test_cambio_de_archivos_tras_revision_detiene_la_activacion(self):
        respuestas = iter(['Admin', 'REVISADO', 'REVISADO'])
        def leer(pregunta):
            if pregunta.startswith('Para continuar'):
                ruta = self.archivos.historiales / self.ana.id / 'historial.csv'
                ruta.parent.mkdir(parents=True, exist_ok=True)
                with ruta.open('w', encoding='utf-8', newline='') as f:
                    csv.DictWriter(f, fieldnames=CAMPOS).writeheader()
                return 'MIGRAR'
            return next(respuestas)
        self.assertEqual(self.ejecutar([], leer=leer), 1)
        self.assertTrue(verificar_vinculo_csv(self.archivos.cuentas, self.archivos.historiales))
        with closing(sqlite3.connect(self.archivos.cuentas)) as conexion:
            self.assertEqual(conexion.execute('SELECT count(*) FROM revisiones_csv_ausente').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
