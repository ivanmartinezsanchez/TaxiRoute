"""Pruebas del lanzador, sin Docker ni correo reales."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from taxiroute.arrancar_windows import ErrorArranque, arrancar


class PruebasLanzador(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.raiz = Path(temp.name); self.datos = self.raiz / "datos"; self.datos.mkdir()
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            (self.datos / nombre).write_text("ficticio", encoding="utf-8")
        self.cert = self.raiz / "cert.pem"; self.cert.write_text("ficticio", encoding="utf-8")
        self.clave = self.raiz / "clave.pem"; self.clave.write_text("ficticio", encoding="utf-8")
        self.argumentos = (self.datos, self.cert, self.clave)
        for nombre in ("_esperar_https", "_verificar_contenedor", "_estado_archivo", "cargar", "webbrowser.open", "ssl.SSLContext"):
            parche = patch("taxiroute.arrancar_windows." + nombre); setattr(self, nombre.replace(".", "_"), parche.start()); self.addCleanup(parche.stop)
        self._verificar_contenedor.return_value = "a" * 64
        self._estado_archivo.return_value = "ausente"
        self.webbrowser_open.return_value = True

    @patch("taxiroute.arrancar_windows.subprocess.run")
    @patch("taxiroute.arrancar_windows._docker")
    def test_primera_vez_arranca_y_carga(self, docker, proceso):
        docker.return_value = b""; proceso.return_value = Mock(returncode=0)
        resultado = arrancar(*self.argumentos)
        self.assertIn("cargada", resultado)
        self.assertIn("--no-recreate", proceso.call_args.args[0])
        self.cargar.assert_called_once(); self.webbrowser_open.assert_called_once()

    @patch("taxiroute.arrancar_windows.subprocess.run")
    @patch("taxiroute.arrancar_windows._docker")
    def test_existente_no_recrea_ni_vuelve_a_cargar(self, docker, proceso):
        docker.side_effect = [("a" * 64 + "\n").encode(), json.dumps([{"State":{"Running":True}}]).encode()]
        self._estado_archivo.return_value = "presente"
        resultado = arrancar(*self.argumentos)
        self.assertIn("no se ha comprobado a qué cuenta pertenece ni su vigencia", resultado)
        proceso.assert_not_called(); self.cargar.assert_not_called()

    @patch("taxiroute.arrancar_windows.subprocess.run")
    @patch("taxiroute.arrancar_windows._docker")
    def test_existente_detenido_se_verifica_antes_de_arrancar(self, docker, proceso):
        docker.side_effect = [("a" * 64 + "\n").encode(), json.dumps([{"State":{"Running":False}}]).encode(), b""]
        arrancar(*self.argumentos)
        self.assertEqual(docker.call_args.args[0], ["start", "a" * 64])
        self._verificar_contenedor.assert_any_call(permitir_archivo=True, permitir_detenido=True,
            rutas_esperadas={"/datos":self.datos.resolve(), "/run/secrets/tls_certificado":self.cert.resolve(),
                             "/run/secrets/tls_clave":self.clave.resolve()})
        proceso.assert_not_called(); self.cargar.assert_called_once()

    @patch("taxiroute.arrancar_windows.subprocess.run")
    @patch("taxiroute.arrancar_windows._docker")
    def test_destino_invalido_no_arranca_ni_abre(self, docker, proceso):
        docker.return_value = ("a" * 64 + "\n").encode()
        self._verificar_contenedor.side_effect = RuntimeError("detalle privado")
        with self.assertRaises(RuntimeError): arrancar(*self.argumentos)
        proceso.assert_not_called(); self.cargar.assert_not_called(); self.webbrowser_open.assert_not_called()

    @patch("taxiroute.arrancar_windows.subprocess.run")
    @patch("taxiroute.arrancar_windows._docker")
    def test_fallo_de_carga_no_abre_navegador(self, docker, proceso):
        from taxiroute.cargar_gmail_windows import ErrorCarga
        docker.return_value = b""; proceso.return_value = Mock(returncode=0)
        self.cargar.side_effect = ErrorCarga("detalle privado")
        with self.assertRaises(ErrorArranque): arrancar(*self.argumentos)
        self.webbrowser_open.assert_not_called()


class PruebasEspera(TestCase):
    @patch("taxiroute.arrancar_windows.time.sleep")
    @patch("taxiroute.arrancar_windows.time.monotonic", side_effect=[0, 31])
    @patch("taxiroute.arrancar_windows.build_opener")
    def test_espera_termina_si_servidor_devuelve_codigo_inesperado(self, construir, reloj, dormir):
        from taxiroute.arrancar_windows import _esperar_https
        respuesta = construir.return_value.open.return_value.__enter__.return_value
        respuesta.status = 204
        with patch("taxiroute.arrancar_windows.ssl.create_default_context"):
            with self.assertRaises(ErrorArranque): _esperar_https(Path("certificado-ficticio.pem"))
        dormir.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
