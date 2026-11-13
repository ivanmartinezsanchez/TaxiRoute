"""Comprobaciones aisladas del lanzador; no usa Gmail ni Docker."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from taxiroute.arranque_proxy_experimental import _validar_certificados
from taxiroute.iniciar_proxy_local_experimental import _crear_tls_interno, _leer_correo, main


class PruebasInicioProxy(unittest.TestCase):
    def setUp(self):
        self.temporal = TemporaryDirectory(prefix="inicio-proxy-")
        self.addCleanup(self.temporal.cleanup)
        self.carpeta = Path(self.temporal.name) / "instalacion"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        prefijo = "taxiroute.iniciar_proxy_local_experimental."
        for nombre in ("_crear", "revisar_instalacion", "_crear_tls_interno", "iniciar_y_registrar",
                       "reutilizar", "verificar_permisos_windows", "webbrowser.open"):
            nombre_atributo = nombre.replace("webbrowser.open", "navegador").lstrip("_")
            setattr(self, nombre_atributo, self.stack.enter_context(patch(prefijo + nombre)))
        self.reutilizar.return_value = SimpleNamespace(correo="ausente", servicios_recuperados=False)
        self.crear_tls_interno.return_value = (Path("interno.pem"), Path("interno.key"))

    def rutas(self):
        return SimpleNamespace(carpeta=self.carpeta, datos=self.carpeta / "datos",
                               certificado=self.carpeta / "cert.pem", clave=self.carpeta / "clave.pem")

    def ejecutar(self):
        salida, errores = StringIO(), StringIO()
        with redirect_stdout(salida), redirect_stderr(errores):
            codigo = main(["--carpeta", str(self.carpeta), "--no-abrir"])
        return codigo, salida.getvalue(), errores.getvalue()

    def test_instalacion_nueva_se_registra_y_no_abre_ni_conecta_correo(self):
        def preparar(carpeta, *, comprobar_requisitos):
            self.assertTrue(callable(comprobar_requisitos))
            carpeta.mkdir()
            return self.rutas()
        self.crear.side_effect = preparar
        codigo, salida, errores = self.ejecutar()
        self.assertEqual((codigo, errores), (0, ""))
        self.assertIn("Correo administrativo no conectado", salida)
        self.iniciar_y_registrar.assert_called_once()
        self.reutilizar.assert_called_once_with(self.carpeta / "proxy-local")
        self.navegador.assert_not_called()

    def test_instalacion_preparada_se_reutiliza_sin_regenerar(self):
        identidad = self.carpeta / "proxy-local"
        identidad.mkdir(parents=True)
        (identidad / "manifiesto-proxy.json").write_text("ficticio", encoding="utf-8")
        (identidad / "manifiesto-proxy.completo").write_text("ficticio", encoding="utf-8")
        self.revisar_instalacion.return_value = self.rutas()
        codigo, _, _ = self.ejecutar()
        self.assertEqual(codigo, 0)
        self.crear.assert_not_called()
        self.crear_tls_interno.assert_not_called()
        self.iniciar_y_registrar.assert_not_called()
        self.reutilizar.assert_called_once_with(identidad)

    def test_instalacion_previa_sin_identidad_no_se_adopta(self):
        self.carpeta.mkdir()
        self.revisar_instalacion.return_value = self.rutas()
        codigo, _, _ = self.ejecutar()
        self.assertEqual(codigo, 1)
        self.crear.assert_not_called()
        self.iniciar_y_registrar.assert_not_called()
        self.reutilizar.assert_not_called()

    def test_no_cambia_proyecto_al_reutilizar(self):
        self.carpeta.mkdir()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            codigo = main(["--carpeta", str(self.carpeta), "--proyecto", "otra", "--no-abrir"])
        self.assertEqual(codigo, 1)
        self.revisar_instalacion.assert_not_called()
        self.reutilizar.assert_not_called()

    def test_permisos_no_validos_impiden_creacion(self):
        from taxiroute.permisos_proxy_windows_experimental import PermisosProxyError
        self.verificar_permisos_windows.side_effect = PermisosProxyError("fallo")
        codigo, _, errores = self.ejecutar()
        self.assertEqual(codigo, 1)
        self.assertIn("permisos", errores)
        self.crear.assert_not_called()
        self.iniciar_y_registrar.assert_not_called()
        self.reutilizar.assert_not_called()

    def test_gmail_se_consulta_solo_si_se_solicita_y_tras_comprobar_permisos(self):
        identidad = self.carpeta / "proxy-local"
        identidad.mkdir(parents=True)
        (identidad / "manifiesto-proxy.json").write_text("ficticio", encoding="utf-8")
        (identidad / "manifiesto-proxy.completo").write_text("ficticio", encoding="utf-8")
        self.revisar_instalacion.return_value = self.rutas()
        def ejecutar_carga(_identidad, *, proveedor):
            self.assertEqual(proveedor(), "contenido-ficticio")
            return SimpleNamespace(correo="cargado", servicios_recuperados=False)
        self.reutilizar.side_effect = ejecutar_carga
        with patch("taxiroute.iniciar_proxy_local_experimental._almacen_windows") as almacen, \
             redirect_stdout(StringIO()) as salida, redirect_stderr(StringIO()):
            almacen.return_value.get_password.return_value = "contenido-ficticio"
            codigo = main(["--carpeta", str(self.carpeta), "--conectar-gmail", "--no-abrir"])
        self.assertEqual(codigo, 0)
        self.assertIn("Perfil de Gmail cargado", salida.getvalue())
        almacen.return_value.get_password.assert_called_once()
        self.assertGreaterEqual(self.verificar_permisos_windows.call_count, 3)

    def test_lectura_no_accede_al_almacen_si_fallan_permisos(self):
        from taxiroute.permisos_proxy_windows_experimental import PermisosProxyError
        self.verificar_permisos_windows.side_effect = PermisosProxyError("fallo")
        with patch("taxiroute.iniciar_proxy_local_experimental._almacen_windows") as almacen:
            with self.assertRaises(PermisosProxyError):
                _leer_correo(self.carpeta)
        almacen.assert_not_called()


class PruebasCertificadoInterno(unittest.TestCase):
    def test_certificado_identifica_ip_y_dns_y_no_sobrescribe(self):
        with TemporaryDirectory(prefix="cert-proxy-") as t:
            destino = Path(t) / "tls"
            certificado, clave = _crear_tls_interno(destino)
            _validar_certificados(certificado, clave, interno=True)
            original = clave.read_bytes()
            with self.assertRaises(FileExistsError):
                _crear_tls_interno(destino)
            self.assertEqual(clave.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
