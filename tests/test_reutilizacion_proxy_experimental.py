"""Pruebas aisladas; no acceden a Docker ni al almacén de credenciales."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from taxiroute.arranque_proxy_experimental import ErrorArranqueProxy
from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, CargaProxyPosibleExposicion
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.reutilizacion_proxy_experimental import ErrorReutilizacion, _bloqueo_instalacion, iniciar_y_registrar, reutilizar
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


def evidencia(backend=True, proxy=True):
    return EstadoComposicion(EstadoContenedor("1"*64, "sha256:"+"a"*64, "inicio", 0, backend),
                             EstadoContenedor("2"*64, "sha256:"+"b"*64, "inicio", 0, proxy))


class PruebasReutilizacion(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        modulo = "taxiroute.reutilizacion_proxy_experimental."
        for nombre in ("_bloqueo_instalacion", "leer_manifiesto", "_validar_certificados", "_claves_distintas",
                       "verificar_para_reutilizar", "verificar_composicion", "_docker",
                       "_esperar_https", "_estado_archivo", "cargar_proxy_experimental",
                       "arrancar_proxy", "crear_manifiesto"):
            setattr(self, nombre.lstrip("_"), self.stack.enter_context(patch(modulo + nombre)))
        self.bloqueo_instalacion.side_effect = lambda _: nullcontext()
        self.rutas = {k: "/ficticio/" + k for k in ("datos", "cert_interno", "clave_interna", "cert_externo", "clave_externa")}
        self.ids = {"backend": "1"*64, "proxy": "2"*64}
        self.manifiesto = SimpleNamespace(proyecto="taxi-prueba", rutas=self.rutas,
            imagenes={"backend": "sha256:"+"a"*64, "proxy": "sha256:"+"b"*64}, contenedores=self.ids)
        self.leer_manifiesto.return_value = self.manifiesto
        self.verificar_para_reutilizar.return_value = evidencia()
        self.verificar_composicion.return_value = evidencia()
        self.estado_archivo.return_value = "presente"

    def test_activos_responden_sin_reiniciar_ni_leer_proveedor(self):
        proveedor = Mock()
        resultado = reutilizar("instalacion", proveedor=proveedor)
        self.assertEqual(resultado.correo, "presente")
        self.assertFalse(resultado.servicios_recuperados)
        self.docker.assert_not_called()
        proveedor.assert_not_called()
        self.cargar_proxy_experimental.assert_not_called()

    def test_ambos_detenidos_arrancan_en_orden_y_cargan_solo_si_se_pide(self):
        self.verificar_para_reutilizar.return_value = evidencia(False, False)
        self.estado_archivo.return_value = "ausente"
        self.cargar_proxy_experimental.return_value = evidencia()
        proveedor = Mock()
        resultado = reutilizar("instalacion", proveedor=proveedor)
        self.assertEqual([x.args[0] for x in self.docker.call_args_list],
                         [["start", self.ids["backend"]], ["start", self.ids["proxy"]]])
        self.assertEqual(resultado.correo, "cargado")
        self.assertTrue(resultado.servicios_recuperados)
        self.cargar_proxy_experimental.assert_called_once()

    def test_backend_activo_y_proxy_detenido_solo_inicia_proxy(self):
        self.verificar_para_reutilizar.return_value = evidencia(True, False)
        reutilizar("instalacion")
        self.docker.assert_called_once_with(["start", self.ids["proxy"]])

    def test_backend_detenido_proxy_activo_se_detiene_y_arranca_en_orden(self):
        self.verificar_para_reutilizar.side_effect = [evidencia(False, True), evidencia(False, True), evidencia(False, False)]
        reutilizar("instalacion")
        self.assertEqual([x.args[0] for x in self.docker.call_args_list],
                         [["stop", "--time", "3", self.ids["proxy"]], ["start", self.ids["backend"]],
                          ["start", self.ids["proxy"]]])

    def test_si_backend_cambia_al_detener_proxy_no_se_reinicia(self):
        self.verificar_para_reutilizar.side_effect = [evidencia(False, True), evidencia(False, True), evidencia(True, False)]
        with self.assertRaises(ErrorReutilizacion):
            reutilizar("instalacion")
        self.assertEqual([x.args[0] for x in self.docker.call_args_list],
                         [["stop", "--time", "3", self.ids["proxy"]]])

    def test_cambio_entre_comprobaciones_impide_arrancar(self):
        self.verificar_para_reutilizar.side_effect = [evidencia(False, False), evidencia(True, False)]
        with self.assertRaises(ErrorReutilizacion):
            reutilizar("instalacion")
        self.docker.assert_not_called()

    def test_https_o_identidad_invalida_no_lee_proveedor(self):
        self.esperar_https.side_effect = OSError("fallo")
        proveedor = Mock()
        with self.assertRaises(ErrorReutilizacion):
            reutilizar("instalacion", proveedor=proveedor)
        proveedor.assert_not_called()
        self.cargar_proxy_experimental.assert_not_called()

    def test_sin_https_reinicia_solo_proxy_y_verifica_otra_vez(self):
        self.esperar_https.side_effect = [ErrorArranqueProxy("sin respuesta"), None]
        resultado = reutilizar("instalacion")
        self.assertEqual(resultado.correo, "presente")
        self.docker.assert_called_once_with(["restart", "--time", "3", self.ids["proxy"]])
        self.assertEqual(self.esperar_https.call_count, 2)

    def test_sin_https_con_identidad_cambiada_no_reinicia(self):
        self.esperar_https.side_effect = ErrorArranqueProxy("sin respuesta")
        self.verificar_composicion.side_effect = [evidencia(), ValueError("cambio")]
        proveedor = Mock()
        with self.assertRaises(ErrorReutilizacion):
            reutilizar("instalacion", proveedor=proveedor)
        self.docker.assert_not_called()
        proveedor.assert_not_called()

    def test_despues_de_arrancar_no_reinicia_proxy_si_https_falla(self):
        self.verificar_para_reutilizar.return_value = evidencia(False, False)
        self.esperar_https.side_effect = ErrorArranqueProxy("sin respuesta")
        with self.assertRaises(ErrorReutilizacion):
            reutilizar("instalacion")
        self.assertEqual([x.args[0] for x in self.docker.call_args_list],
                         [["start", self.ids["backend"]], ["start", self.ids["proxy"]]])

    def test_posible_exposicion_no_se_oculta(self):
        self.estado_archivo.return_value = "ausente"
        self.cargar_proxy_experimental.side_effect = CargaProxyPosibleExposicion("envío posible")
        with self.assertRaises(CargaProxyPosibleExposicion):
            reutilizar("instalacion", proveedor=Mock())

    def test_fallo_antes_de_enviar_se_explica_sin_datos_privados(self):
        self.estado_archivo.return_value = "ausente"
        self.cargar_proxy_experimental.side_effect = CargaProxyAntesEnvio("dato privado")
        with self.assertRaises(ErrorReutilizacion) as error:
            reutilizar("instalacion", proveedor=Mock())
        self.assertIn("antes de enviarlo", str(error.exception))
        self.assertNotIn("dato privado", str(error.exception))

    def test_inicio_no_sobrescribe_manifiesto_existente(self):
        with TemporaryDirectory() as t:
            Path(t, "manifiesto-proxy.json").write_text("conservar", encoding="utf-8")
            with self.assertRaises(ErrorReutilizacion):
                iniciar_y_registrar(t, proyecto="taxi-prueba", rutas=self.rutas)
            self.arrancar_proxy.assert_not_called()

    def test_bloqueo_ocupado_impide_leer_y_modificar_servicios(self):
        with TemporaryDirectory() as t:
            with bloqueo_historial(Path(t) / ".reutilizacion-proxy", espera=0):
                with patch("taxiroute.reutilizacion_proxy_experimental._bloqueo_instalacion", side_effect=_bloqueo_instalacion):
                    with self.assertRaises(ErrorReutilizacion):
                        reutilizar(t)
            self.leer_manifiesto.assert_not_called()
            self.docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
