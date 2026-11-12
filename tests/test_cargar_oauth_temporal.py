"""Comprueba la carga temporal usando únicamente datos ficticios."""
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.cargar_oauth_temporal import NOMBRE, cargar
from taxiroute.oauth_gmail import ALCANCE_ENVIO


class PruebasCargaOAuthTemporal(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        if os.name == "posix":
            self.carpeta.chmod(0o700)
        self.datos = json.dumps({"client_id":"id-ficticio", "client_secret":"secreto-ficticio",
            "refresh_token":"token-ficticio", "token_uri":"https://oauth2.googleapis.com/token",
            "scopes":[ALCANCE_ENVIO]}).encode()

    def test_carga_completa_no_permite_sobrescribir(self):
        cargar(BytesIO(self.datos), self.carpeta)
        destino = self.carpeta / NOMBRE
        self.assertEqual(destino.read_bytes(), self.datos)
        if os.name == "posix":
            self.assertEqual(destino.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            cargar(BytesIO(self.datos), self.carpeta)
        self.assertEqual(destino.read_bytes(), self.datos)
        self.assertEqual([p.name for p in self.carpeta.iterdir()], [NOMBRE])

    def test_rechaza_entrada_invalida_y_excesiva_sin_crear_archivos(self):
        for datos in (b"", b"invalido", b"x" * 32769):
            with self.subTest(longitud=len(datos)), self.assertRaises(ValueError) as fallo:
                cargar(BytesIO(datos), self.carpeta)
            self.assertNotIn("token-ficticio", str(fallo.exception))
        self.assertEqual(list(self.carpeta.iterdir()), [])

    def test_error_al_publicar_limpia_archivo_temporal(self):
        with patch("taxiroute.cargar_oauth_temporal.os.link", side_effect=OSError("detalle privado")):
            with self.assertRaises(ValueError) as fallo:
                cargar(BytesIO(self.datos), self.carpeta)
        self.assertNotIn("detalle privado", str(fallo.exception))
        self.assertEqual(list(self.carpeta.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "Los permisos POSIX se prueban en Linux")
    def test_rechaza_directorio_con_permisos_amplios(self):
        self.carpeta.chmod(0o755)
        with self.assertRaises(ValueError):
            cargar(BytesIO(self.datos), self.carpeta)
        self.assertEqual(list(self.carpeta.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
