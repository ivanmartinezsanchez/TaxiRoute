from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch
import sqlite3
import os

from taxiroute.recuperar_copia_experimental import restaurar_con_codigos
from taxiroute.copia_sqlite_experimental import CopiaSQLiteError, crear_copia_sqlite
from taxiroute.instalacion_inicial import preparar_instalacion
from taxiroute.recuperacion_admin_experimental import recuperar_administrador


@skipUnless(os.name == "nt", "El ensayo de publicación sin reemplazo solo admite Windows")
class PruebasRestauracionConCodigos(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix="taxi-ensayo-")
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name).resolve()
        self.anteriores = []
        instalacion = preparar_instalacion(
            self.base / "origen", usuario="AdminPrueba", nombre="Prueba",
            correo="prueba@example.invalid", contrasena="Contrasena123!",
            confirmar_codigos=lambda codigos: self.anteriores.extend(codigos) or True,
        )
        self.origen = instalacion.datos
        with closing(sqlite3.connect(self.origen / "cuentas.sqlite3")) as c, c:
            c.execute("UPDATE usuarios SET bloqueado_en=?, intentos_fallidos=15", ("2026-01-01T00:00:00+00:00",))
        self.copia, self.destino = self.base / "copia", self.base / "destino"
        crear_copia_sqlite(self.origen, self.copia)

    def ejecutar(self, **cambios):
        opciones = dict(estado_actual=self.origen, usuario="AdminPrueba", contrasena="Contrasena123!", codigo=self.anteriores[0],
                        confirmar_codigos=lambda codigos: True)
        opciones.update(cambios)
        return restaurar_con_codigos(self.copia, self.destino, **opciones)

    def test_restaura_bloqueada_y_el_nuevo_codigo_permite_recuperar(self):
        nuevos = []
        self.ejecutar(confirmar_codigos=lambda codigos: nuevos.extend(codigos) or True)
        cuentas = self.destino / "cuentas.sqlite3"
        with closing(sqlite3.connect(cuentas)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM codigos_recuperacion WHERE invalidado_en IS NULL").fetchone()[0], 5)
            self.assertEqual(c.execute("SELECT count(*) FROM codigos_recuperacion WHERE invalidado_en IS NOT NULL").fetchone()[0], 5)
            self.assertIsNotNone(c.execute("SELECT bloqueado_en FROM usuarios").fetchone()[0])
        recuperar_administrador(cuentas, "AdminPrueba", "Contrasena123!", nuevos[0])
        with closing(sqlite3.connect(cuentas)) as c:
            self.assertIsNone(c.execute("SELECT bloqueado_en FROM usuarios").fetchone()[0])
        with closing(sqlite3.connect(self.origen / "cuentas.sqlite3")) as c:
            self.assertIsNotNone(c.execute("SELECT bloqueado_en FROM usuarios").fetchone()[0])
            self.assertEqual(c.execute("SELECT count(*) FROM codigos_recuperacion WHERE invalidado_en IS NULL").fetchone()[0], 5)

    def test_credencial_incorrecta_no_crea_destino_ni_muestra_codigos(self):
        nuevos = []
        with self.assertRaises(CopiaSQLiteError):
            self.ejecutar(contrasena="Incorrecta123!", confirmar_codigos=lambda c: nuevos.extend(c) or True)
        self.assertFalse(nuevos)
        self.assertFalse(self.destino.exists())

    def test_cancelacion_no_crea_destino(self):
        with self.assertRaises(CopiaSQLiteError):
            self.ejecutar(confirmar_codigos=lambda codigos: False)
        self.assertFalse(self.destino.exists())

    def test_cuenta_deshabilitada_no_se_reactiva(self):
        for carpeta in (self.origen, self.copia):
            with closing(sqlite3.connect(carpeta / "cuentas.sqlite3")) as c, c:
                c.execute("UPDATE usuarios SET habilitado=0")
        with self.assertRaises(CopiaSQLiteError):
            self.ejecutar()
        self.assertFalse(self.destino.exists())

    def test_cambio_en_copia_durante_confirmacion_no_activa_codigos_nuevos(self):
        def alterar(codigos):
            with closing(sqlite3.connect(self.copia / "cuentas.sqlite3")) as c, c:
                c.execute("UPDATE codigos_recuperacion SET invalidado_en='2026' WHERE invalidado_en IS NULL")
            return True
        with self.assertRaises(CopiaSQLiteError):
            self.ejecutar(confirmar_codigos=alterar)
        self.assertFalse(self.destino.exists())

    def test_rechaza_copia_antigua_sin_mostrar_codigos(self):
        with closing(sqlite3.connect(self.origen / "cuentas.sqlite3")) as c, c:
            c.execute("UPDATE usuarios SET version_credenciales=version_credenciales+1")
        nuevos = []
        with self.assertRaises(CopiaSQLiteError):
            self.ejecutar(confirmar_codigos=lambda c: nuevos.extend(c) or True)
        self.assertFalse(nuevos)
        self.assertFalse(self.destino.exists())

    def test_interrupcion_deja_carpeta_incompleta_sin_destino_final(self):
        with patch("taxiroute.recuperar_copia_experimental._copiar_archivos", side_effect=OSError("fallo ficticio")):
            with self.assertRaises(OSError):
                self.ejecutar()
        self.assertFalse(self.destino.exists())
        self.assertEqual(len(list(self.base.glob("destino.incompleta-*"))), 1)
