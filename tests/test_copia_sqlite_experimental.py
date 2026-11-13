"""Pruebas aisladas de copias SQLite con instalaciones ficticias."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from taxiroute.copia_sqlite_experimental import (
    CopiaSQLiteError, MARCADOR, crear_copia_sqlite, restaurar_copia_sqlite,
)
from taxiroute.instalacion_inicial import preparar_instalacion
from taxiroute.bloqueo import bloqueo_historial, HistorialOcupado


class PruebasCopiaSQLite(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory(prefix="taxi-copia-")
        self.addCleanup(self.temporal.cleanup)
        self.base = Path(self.temporal.name).resolve()
        self.codigos = []
        def confirmar_codigos(codigos):
            self.codigos.extend(codigos)
            return len(codigos) == 5

        self.datos = preparar_instalacion(
            self.base / "instalacion", usuario="AdminPrueba", nombre="Administrador prueba",
            correo="demo@example.invalid", contrasena="SeguraPrueba123!",
            confirmar_codigos=confirmar_codigos,
        ).datos
        self.copia = self.base / "copia"
        self.restaurada = self.base / "restaurada"

    def test_copia_y_restauracion_revocan_solo_en_la_restauracion(self):
        cuentas = self.datos / "cuentas.sqlite3"
        with closing(sqlite3.connect(cuentas)) as c, c:
            usuario = c.execute("SELECT id FROM usuarios").fetchone()[0]
            c.execute("INSERT INTO sesiones VALUES (?, ?, ?, 1, 'administrador', NULL, ?, ?, ?, NULL)",
                      ("sesion", "token", usuario, "2026", "2026", "2099"))
            c.execute("INSERT INTO desafios_acceso VALUES (?, ?, ?, 1, ?, ?, ?, NULL, NULL, 0, 'enviado')",
                      ("desafio", usuario, "pin", "demo@example.invalid", "2026", "2099"))
            c.execute("INSERT INTO autorizaciones_cambio VALUES (?, ?, ?, ?, NULL)",
                      ("autorizacion", "desafio", "2026", "2099"))
        crear_copia_sqlite(self.datos, self.copia)
        self.assertTrue((self.copia / MARCADOR).is_file())
        restaurar_copia_sqlite(self.copia, self.restaurada)
        for ruta, revocada in ((cuentas, False), (self.copia / "cuentas.sqlite3", False),
                               (self.restaurada / "cuentas.sqlite3", True)):
            with closing(sqlite3.connect(ruta)) as c:
                for tabla, columna in (("sesiones", "revocada_en"), ("desafios_acceso", "invalidado_en"),
                                       ("autorizaciones_cambio", "consumido_en"),
                                       ("codigos_recuperacion", "invalidado_en")):
                    filas = c.execute(f"SELECT {columna} FROM {tabla}").fetchall()
                    self.assertTrue(filas)
                    self.assertTrue(all((fila[0] is not None) == revocada for fila in filas))

    def test_restauracion_rechaza_admin_bloqueado_sin_crear_destino(self):
        cuentas = self.datos / "cuentas.sqlite3"
        with closing(sqlite3.connect(cuentas)) as c, c:
            c.execute("""UPDATE usuarios SET intentos_fallidos=15, bloqueado_en=?,
                         proximo_intento_en=NULL WHERE rol='administrador'""",
                      ("2026-11-01T00:00:00+00:00",))
        crear_copia_sqlite(self.datos, self.copia)
        with self.assertRaisesRegex(CopiaSQLiteError, "administrador habilitado y desbloqueado"):
            restaurar_copia_sqlite(self.copia, self.restaurada)
        self.assertFalse(self.restaurada.exists())
        self.assertTrue((self.copia / MARCADOR).is_file())
        with closing(sqlite3.connect(cuentas)) as c:
            self.assertEqual(c.execute("SELECT intentos_fallidos FROM usuarios").fetchone()[0], 15)
            self.assertEqual(c.execute("SELECT count(*) FROM codigos_recuperacion WHERE invalidado_en IS NULL").fetchone()[0], 5)

    def test_restauracion_rechaza_admin_deshabilitado(self):
        with closing(sqlite3.connect(self.datos / "cuentas.sqlite3")) as c, c:
            c.execute("UPDATE usuarios SET habilitado=0 WHERE rol='administrador'")
        crear_copia_sqlite(self.datos, self.copia)
        with self.assertRaisesRegex(CopiaSQLiteError, "administrador habilitado y desbloqueado"):
            restaurar_copia_sqlite(self.copia, self.restaurada)
        self.assertFalse(self.restaurada.exists())

    def test_fallo_de_copia_deja_carpeta_incompleta_sin_marcar(self):
        with patch("taxiroute.copia_sqlite_experimental._copiar_archivos", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                crear_copia_sqlite(self.datos, self.copia)
        self.assertTrue(self.copia.is_dir())
        self.assertFalse((self.copia / MARCADOR).exists())
        with self.assertRaises(CopiaSQLiteError):
            restaurar_copia_sqlite(self.copia, self.restaurada)

    def test_rechaza_destino_existente_y_dentro_del_origen(self):
        self.copia.mkdir()
        (self.copia / "no_tocar").write_text("conservar", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            crear_copia_sqlite(self.datos, self.copia)
        self.assertEqual((self.copia / "no_tocar").read_text(encoding="utf-8"), "conservar")
        with self.assertRaises(CopiaSQLiteError):
            crear_copia_sqlite(self.datos, self.datos / "copia")

    def test_rechaza_base_deteriorada_y_marcador_modificado(self):
        crear_copia_sqlite(self.datos, self.copia)
        (self.copia / MARCADOR).write_text("alterado", encoding="utf-8")
        with self.assertRaises(CopiaSQLiteError):
            restaurar_copia_sqlite(self.copia, self.restaurada)
        self.assertFalse(self.restaurada.exists())
        (self.datos / "historial.sqlite3").write_bytes(b"no es sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            crear_copia_sqlite(self.datos, self.base / "otra")
        self.assertFalse((self.base / "otra").exists())

    def test_rechaza_operaciones_pendientes(self):
        with closing(sqlite3.connect(self.datos / "cuentas.sqlite3")) as c, c:
            usuario = c.execute("SELECT id FROM usuarios").fetchone()[0]
            c.execute("INSERT INTO intenciones_operacion VALUES (?, ?, 'iniciar', 0, NULL, ?)",
                      ("intencion", usuario, "2026"))
        with self.assertRaises(CopiaSQLiteError):
            crear_copia_sqlite(self.datos, self.copia)
        self.assertFalse(self.copia.exists())

    def test_rechaza_carrera_activa(self):
        with closing(sqlite3.connect(self.datos / "cuentas.sqlite3")) as c, c:
            usuario = c.execute("SELECT id FROM usuarios").fetchone()[0]
            c.execute("""INSERT INTO carreras_recuperacion
                (id, usuario_id, estado_registro, estado_vehiculo, inicio, ultimo_control,
                 parado_ns, movimiento_ns, tarifa_parado, tarifa_movimiento, revision)
                VALUES (?, ?, 'activa', 'parado', ?, ?, 0, 0, '0.02', '0.05', 0)""",
                ("carrera", usuario, "2026", "2026"))
        with self.assertRaises(CopiaSQLiteError):
            crear_copia_sqlite(self.datos, self.copia)
        self.assertFalse(self.copia.exists())

    def test_rechaza_cuando_el_bloqueo_esta_ocupado(self):
        with bloqueo_historial(self.datos / "servidor", espera=0):
            with self.assertRaises(HistorialOcupado):
                crear_copia_sqlite(self.datos, self.copia)
        self.assertFalse(self.copia.exists())


if __name__ == "__main__":
    unittest.main()
