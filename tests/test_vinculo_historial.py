"""Vínculo entre instalaciones experimentales con datos temporales."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from secrets import token_bytes

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.vinculo_historial import (MARCADOR_HISTORIAL, VinculoHistorialError,
    crear_vinculo_csv_nuevo, rechazar_base_vinculada, verificar_vinculo_csv)


class PruebasVinculoCSV(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name)

    def test_preparacion_nueva_vincula_y_reabre(self):
        archivos = preparar_archivos_csv(self.raiz / "primera")
        self.assertTrue(verificar_vinculo_csv(archivos.cuentas, archivos.historiales))
        with conectar(archivos.cuentas) as conexion:
            fila = conexion.execute("SELECT formato, identificador, estado FROM vinculo_historial").fetchone()
        self.assertEqual((fila["formato"], fila["estado"]), ("csv", "listo"))
        self.assertEqual((archivos.historiales / MARCADOR_HISTORIAL).read_text(encoding="ascii"), fila["identificador"] + "\n")
        with self.assertRaises(VinculoHistorialError):
            rechazar_base_vinculada(archivos.cuentas)

    def test_historial_de_otra_instalacion_y_marcador_ausente_se_rechazan(self):
        primera = preparar_archivos_csv(self.raiz / "primera")
        segunda = preparar_archivos_csv(self.raiz / "segunda")
        with self.assertRaises(VinculoHistorialError):
            verificar_vinculo_csv(primera.cuentas, segunda.historiales)
        marcador = primera.historiales / MARCADOR_HISTORIAL
        marcador.unlink()
        with self.assertRaises(VinculoHistorialError):
            verificar_vinculo_csv(primera.cuentas, primera.historiales)
        self.assertFalse(marcador.exists())

    def test_no_adopta_historial_anterior_ni_base_con_cuentas(self):
        carpeta = self.raiz / "datos"
        carpeta.mkdir()
        base = self.raiz / "cuentas.sqlite3"
        inicializar_base(base)
        ajeno = carpeta / "anterior.csv"
        ajeno.write_text("conservar", encoding="utf-8")
        with self.assertRaises(VinculoHistorialError):
            crear_vinculo_csv_nuevo(base, carpeta)
        self.assertEqual(ajeno.read_text(encoding="utf-8"), "conservar")
        ajeno.unlink()
        from taxiroute.cuentas import crear_cuenta
        crear_cuenta(base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        with self.assertRaises(VinculoHistorialError):
            crear_vinculo_csv_nuevo(base, carpeta)
        self.assertEqual(list(carpeta.iterdir()), [])

    def test_fallo_entre_archivos_deja_preparacion_bloqueada(self):
        carpeta = self.raiz / "datos"
        carpeta.mkdir()
        base = self.raiz / "cuentas.sqlite3"
        inicializar_base(base)
        with patch("taxiroute.vinculo_historial.os.fsync", side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                crear_vinculo_csv_nuevo(base, carpeta)
        with conectar(base) as conexion:
            self.assertEqual(conexion.execute("SELECT estado FROM vinculo_historial").fetchone()[0], "preparando")
        with self.assertRaises(VinculoHistorialError):
            verificar_vinculo_csv(base, carpeta)
        with self.assertRaises(VinculoHistorialError):
            rechazar_base_vinculada(base)

    def test_concurrencia_no_asocia_dos_carpetas(self):
        base = self.raiz / "cuentas.sqlite3"
        inicializar_base(base)
        carpetas = [self.raiz / "uno", self.raiz / "dos"]
        for carpeta in carpetas:
            carpeta.mkdir()
        def crear(carpeta):
            try:
                crear_vinculo_csv_nuevo(base, carpeta)
                return True
            except VinculoHistorialError:
                return False
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(crear, carpetas))
        self.assertEqual(sum(resultados), 1)
        ganadora = carpetas[resultados.index(True)]
        self.assertTrue(verificar_vinculo_csv(base, ganadora))

    def test_instalacion_antigua_no_se_adopta_automaticamente(self):
        base = self.raiz / "cuentas.sqlite3"
        carpeta = self.raiz / "datos"
        carpeta.mkdir()
        inicializar_base(base)
        self.assertFalse(verificar_vinculo_csv(base, carpeta))
        self.assertEqual(list(carpeta.iterdir()), [])
        with conectar(base) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM vinculo_historial").fetchone()[0], 0)

    def test_arranque_csv_no_admite_carpeta_de_otra_instalacion(self):
        from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
        primera = preparar_archivos_csv(self.raiz / "primera")
        segunda = preparar_archivos_csv(self.raiz / "segunda")
        with self.assertRaises(VinculoHistorialError):
            with abrir_app_csv_experimental(ruta_cuentas=primera.cuentas, ruta_tarifas=primera.tarifas,
                    carpeta_historial=segunda.historiales, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None):
                self.fail("No debe abrir una carpeta de otra instalación")

    def test_composicion_sqlite_rechaza_base_vinculada_a_csv(self):
        from taxiroute.composicion_experimental import crear_app_configurada
        from taxiroute.historial_sqlite import HistorialSQLite
        archivos = preparar_archivos_csv(self.raiz / "primera")
        sqlite = self.raiz / "historial.sqlite3"
        HistorialSQLite(sqlite).inicializar()
        with self.assertRaises(VinculoHistorialError):
            crear_app_configurada(ruta_cuentas=archivos.cuentas, ruta_tarifas=archivos.tarifas,
                ruta_historial=sqlite, clave_hmac=token_bytes(32), enviar_pin=lambda *_: None, durable=True)
