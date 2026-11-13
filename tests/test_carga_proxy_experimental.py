"""Pruebas sin Docker ni credenciales reales para la carga con proxy."""
import json
import unittest
from unittest.mock import Mock, patch

from taxiroute.carga_proxy_experimental import (
    CargaProxyAntesEnvio, CargaProxyPosibleExposicion, cargar_proxy_experimental,
)
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


class PruebasCargaProxy(unittest.TestCase):
    def setUp(self):
        backend = EstadoContenedor("a" * 64, "sha256:" + "1" * 64, "2026-11-13T00:00:00Z", 0)
        proxy = EstadoContenedor("b" * 64, "sha256:" + "2" * 64, "2026-11-13T00:00:00Z", 0)
        self.estado = EstadoComposicion(backend, proxy)
        self.ficticio = json.dumps({"client_id": "cliente-ficticio", "client_secret": "clave-ficticia",
            "refresh_token": "token-ficticio", "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": [ALCANCE_ENVIO]})
        self.proveedor = Mock(return_value=self.ficticio)
        self.parametros = {"proyecto": "prueba", "imagenes_esperadas": {}, "rutas_esperadas": {},
                           "evidencia_esperada": self.estado, "proveedor": self.proveedor}
        parche_v = patch("taxiroute.carga_proxy_experimental.verificar_composicion")
        parche_e = patch("taxiroute.carga_proxy_experimental._estado_archivo")
        parche_d = patch("taxiroute.carga_proxy_experimental._docker")
        self.verificar = parche_v.start()
        self.archivo = parche_e.start()
        self.docker = parche_d.start()
        self.addCleanup(parche_v.stop)
        self.addCleanup(parche_e.stop)
        self.addCleanup(parche_d.stop)
        self.verificar.return_value = self.estado
        self.archivo.side_effect = ["ausente", "ausente", "presente"]

    def cargar(self):
        return cargar_proxy_experimental(**self.parametros)

    def test_transmite_por_stdin_al_id_completo_tras_revalidar(self):
        self.assertEqual(self.cargar(), self.estado)
        self.assertEqual(self.verificar.call_count, 3)
        self.assertEqual(self.verificar.call_args_list[0].kwargs["anterior"], self.estado)
        self.assertEqual(self.verificar.call_args_list[1].kwargs["anterior"], self.estado)
        self.assertEqual(self.verificar.call_args_list[2].kwargs["anterior"], self.estado)
        self.proveedor.assert_called_once_with()
        argumentos = self.docker.call_args.args[0]
        self.assertEqual(argumentos[:6], ["exec", "-i", "--user", "10001:10001", "--workdir", "/app"])
        self.assertEqual(argumentos[6], self.estado.backend.identificador)
        self.assertEqual(self.docker.call_args.kwargs["entrada"], self.ficticio.encode())

    def test_verificacion_inicial_o_archivo_existente_impiden_leer(self):
        for motivo in ("verificacion", "archivo"):
            with self.subTest(motivo=motivo):
                self.proveedor.reset_mock()
                self.docker.reset_mock()
                self.verificar.side_effect = ValueError("detalle privado") if motivo == "verificacion" else None
                self.archivo.side_effect = None
                self.archivo.return_value = "presente"
                with self.assertRaises(CargaProxyAntesEnvio) as error:
                    self.cargar()
                self.assertNotIn("privado", str(error.exception))
                self.proveedor.assert_not_called()
                self.docker.assert_not_called()

    def test_cambio_desde_la_evidencia_inicial_impide_leer_o_transmitir(self):
        nuevo_proxy = EstadoContenedor("c" * 64, self.estado.proxy.imagen,
                                       self.estado.proxy.iniciado, 0)
        self.verificar.return_value = EstadoComposicion(self.estado.backend, nuevo_proxy)
        with self.assertRaises(CargaProxyAntesEnvio):
            self.cargar()
        self.proveedor.assert_not_called()
        self.docker.assert_not_called()

    def test_sin_evidencia_valida_rechaza_antes_de_leer(self):
        self.parametros["evidencia_esperada"] = None
        with self.assertRaises(CargaProxyAntesEnvio):
            self.cargar()
        self.verificar.assert_not_called()
        self.proveedor.assert_not_called()
        self.docker.assert_not_called()

    def test_contenido_invalido_o_error_proveedor_no_transmite(self):
        for valor in ("no es json", None, "clave-secreta-en-error"):
            with self.subTest(valor=valor):
                self.proveedor.return_value = valor
                self.archivo.side_effect = None
                self.archivo.return_value = "ausente"
                with self.assertRaises(CargaProxyAntesEnvio) as error:
                    self.cargar()
                self.assertNotIn("clave-secreta-en-error", str(error.exception))
                self.docker.assert_not_called()

    def test_proveedor_no_puede_modificar_las_opciones_de_verificacion(self):
        self.parametros["imagenes_esperadas"]["backend"] = "esperada"
        def proveedor():
            self.parametros["imagenes_esperadas"]["backend"] = "sustituida"
            return self.ficticio
        self.parametros["proveedor"] = proveedor
        self.cargar()
        for llamada in self.verificar.call_args_list:
            self.assertEqual(llamada.kwargs["imagenes_esperadas"]["backend"], "esperada")

    def test_excepcion_del_proveedor_no_revela_texto_ni_transmite(self):
        self.proveedor.side_effect = ValueError("contenido-privado")
        with self.assertRaises(CargaProxyAntesEnvio) as error:
            self.cargar()
        self.assertNotIn("contenido-privado", str(error.exception))
        self.docker.assert_not_called()

    def test_cambio_tras_leer_detiene_antes_de_transmitir(self):
        self.verificar.side_effect = [self.estado, ValueError("cambio")]
        with self.assertRaises(CargaProxyAntesEnvio):
            self.cargar()
        self.proveedor.assert_called_once()
        self.docker.assert_not_called()

    def test_archivo_aparece_tras_leer_detiene_transmision(self):
        self.archivo.side_effect = ["ausente", "presente"]
        with self.assertRaises(CargaProxyAntesEnvio):
            self.cargar()
        self.docker.assert_not_called()

    def test_fallo_transporte_indica_posible_exposicion_sin_revelar_datos(self):
        self.docker.side_effect = ValueError("clave-secreta-en-error")
        with self.assertRaises(CargaProxyPosibleExposicion) as error:
            self.cargar()
        self.assertIn("pudo haberse realizado", str(error.exception))
        self.assertNotIn("clave-secreta-en-error", str(error.exception))
        self.docker.assert_called_once()

    def test_cambio_o_archivo_ausente_despues_indica_posible_exposicion(self):
        for fallo in ("cambio", "archivo"):
            with self.subTest(fallo=fallo):
                self.verificar.side_effect = [self.estado, self.estado, ValueError("cambio")] if fallo == "cambio" else None
                self.archivo.side_effect = ["ausente", "ausente", "ausente"]
                self.docker.reset_mock()
                with self.assertRaises(CargaProxyPosibleExposicion):
                    self.cargar()
                self.docker.assert_called_once()

    def test_proveedor_no_callable_rechazado_sin_comprobar_ni_enviar(self):
        self.parametros["proveedor"] = None
        with self.assertRaises(CargaProxyAntesEnvio):
            self.cargar()
        self.verificar.assert_not_called()
        self.docker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
