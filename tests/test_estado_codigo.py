import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from taxiroute.estado_codigo import ARCHIVOS, codigo_distinto


class PruebasEstadoCodigo(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        self.identificador = "a" * 64
        for nombre in ARCHIVOS:
            (self.carpeta / nombre).write_text("contenido ficticio " + nombre, encoding="utf-8")
        self.resumen = {nombre: hashlib.sha256((self.carpeta / nombre).read_bytes()).hexdigest() for nombre in ARCHIVOS}

    @patch("taxiroute.estado_codigo._docker")
    def test_archivos_iguales_y_distintos(self, docker):
        docker.return_value = json.dumps(self.resumen).encode()
        self.assertFalse(codigo_distinto(self.identificador, carpeta=self.carpeta))
        (self.carpeta / ARCHIVOS[0]).write_text("otro contenido", encoding="utf-8")
        self.assertTrue(codigo_distinto(self.identificador, carpeta=self.carpeta))
        self.assertEqual(docker.call_args.args[0][:3], ["exec", self.identificador, "python"])

    @patch("taxiroute.estado_codigo._docker")
    def test_identificador_invalido_no_consulta_docker(self, docker):
        self.assertIsNone(codigo_distinto("nombre-no-verificado", carpeta=self.carpeta))
        docker.assert_not_called()

    @patch("taxiroute.estado_codigo._docker")
    def test_errores_o_respuestas_incompletas_no_se_presentan_como_igualdad(self, docker):
        for respuesta in (b"no-json", b"{}", json.dumps({k: "incorrecto" for k in ARCHIVOS}).encode()):
            with self.subTest(respuesta=respuesta):
                docker.return_value = respuesta
                self.assertIsNone(codigo_distinto(self.identificador, carpeta=self.carpeta))
        docker.side_effect = RuntimeError("error ficticio")
        self.assertIsNone(codigo_distinto(self.identificador, carpeta=self.carpeta))

    @patch("taxiroute.estado_codigo._docker")
    def test_fichero_local_ausente_es_desconocido(self, docker):
        (self.carpeta / ARCHIVOS[0]).unlink()
        self.assertIsNone(codigo_distinto(self.identificador, carpeta=self.carpeta))
        docker.assert_not_called()
