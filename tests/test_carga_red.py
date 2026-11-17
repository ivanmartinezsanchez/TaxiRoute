"""Pruebas sin Docker ni credenciales reales de la carga explícita de red."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from taxiroute.carga_red import cargar_correo_red, CargaProxyAntesEnvio, CargaProxyPosibleExposicion
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


class PruebasCargaRed(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.identidad = Path(temporal.name)
        self.evidencia = EstadoComposicion(
            EstadoContenedor('1'*64, 'sha256:'+'a'*64, 'inicio', 0, True),
            EstadoContenedor('2'*64, 'sha256:'+'b'*64, 'inicio', 0, True))
        self.manifiesto = SimpleNamespace(rutas={'datos':str(self.identidad),
            'cert_interno':str(self.identidad/'interno'), 'cert_externo':str(self.identidad/'externo')})
        self.proveedor = Mock(return_value='perfil ficticio')
        prefijo = 'taxiroute.carga_red.'
        for nombre, opciones in {
            '_bloqueo_instalacion': {'side_effect': lambda _: nullcontext()},
            '_exigir_sin_actualizacion_pendiente': {}, 'verificar_permisos_windows': {},
            'leer_manifiesto_red': {'return_value':self.manifiesto},
            'verificar_instalacion_red': {'return_value':self.evidencia},
            '_estado_archivo': {'side_effect':['ausente','ausente','presente']},
            '_validar_contenido': {}, '_docker': {},
        }.items():
            setattr(self, nombre, patch(prefijo+nombre, **opciones).start())
        self.addCleanup(patch.stopall)

    def cargar(self):
        return cargar_correo_red(identidad=self.identidad, evidencia_esperada=self.evidencia, proveedor=self.proveedor)

    def test_envio_explicito_comprueba_antes_y_despues(self):
        self.assertEqual(self.cargar(), self.evidencia)
        self.assertEqual(self.verificar_instalacion_red.call_count, 3)
        self._docker.assert_called_once()
        self.assertEqual(self._docker.call_args.kwargs['entrada'], b'perfil ficticio')

    def test_destino_cambiado_no_consulta_proveedor(self):
        self.verificar_instalacion_red.return_value = None
        with self.assertRaises(CargaProxyAntesEnvio): self.cargar()
        self.proveedor.assert_not_called()
        self._docker.assert_not_called()

    def test_archivo_preexistente_no_consulta_proveedor(self):
        self._estado_archivo.side_effect = ['presente']
        with self.assertRaises(CargaProxyAntesEnvio): self.cargar()
        self.proveedor.assert_not_called()
        self._docker.assert_not_called()

    def test_cambio_antes_del_envio_no_transmite(self):
        self.verificar_instalacion_red.side_effect = [self.evidencia, None]
        with self.assertRaises(CargaProxyAntesEnvio): self.cargar()
        self._docker.assert_not_called()

    def test_error_de_transporte_informa_posible_exposicion(self):
        self._docker.side_effect = OSError('fallo ficticio')
        with self.assertRaises(CargaProxyPosibleExposicion): self.cargar()

    def test_cambio_posterior_informa_posible_exposicion(self):
        self.verificar_instalacion_red.side_effect = [self.evidencia, self.evidencia, None]
        with self.assertRaises(CargaProxyPosibleExposicion): self.cargar()

    def test_marcador_impide_acceso_a_proveedor(self):
        self._exigir_sin_actualizacion_pendiente.side_effect = ValueError('pendiente')
        with self.assertRaises(CargaProxyAntesEnvio): self.cargar()
        self.proveedor.assert_not_called()
        self._docker.assert_not_called()

    def test_permisos_cambian_tras_proveedor_no_transmite(self):
        self.verificar_permisos_windows.side_effect = [None]*5 + [PermissionError('fallo')]
        with self.assertRaises(CargaProxyAntesEnvio): self.cargar()
        self._docker.assert_not_called()

    def test_marcador_aparece_tras_envio_informa_exposicion(self):
        self._exigir_sin_actualizacion_pendiente.side_effect = [None, None, None, ValueError('pendiente')]
        with self.assertRaises(CargaProxyPosibleExposicion): self.cargar()
        self._docker.assert_called_once()
