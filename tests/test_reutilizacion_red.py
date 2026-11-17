from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from taxiroute.reutilizacion_red import ErrorReutilizacionRed, reutilizar_red
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


def estado(backend=True, proxy=True):
    return EstadoComposicion(EstadoContenedor('1'*64, 'sha256:'+'a'*64, 'inicio', 0, backend),
                             EstadoContenedor('2'*64, 'sha256:'+'b'*64, 'inicio', 0, proxy))


class PruebasReutilizacionRed(TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        rutas = {k: str(self.carpeta / k) for k in ('datos', 'cert_interno', 'clave_interna', 'cert_externo', 'clave_externa')}
        self.manifiesto = SimpleNamespace(proyecto='prueba', rutas=rutas, direccion='192.168.1.25', puerto=18773,
            imagenes={'backend':'sha256:'+'a'*64, 'proxy':'sha256:'+'b'*64},
            contenedores={'backend':'1'*64, 'proxy':'2'*64})
        self.parches = []
        prefijo = 'taxiroute.reutilizacion_red.'
        for nombre in ('_bloqueo_instalacion','_exigir_sin_actualizacion_pendiente','verificar_permisos_windows',
                       'leer_manifiesto_red','comprobar_direccion_local','_validar_certificados', '_claves_distintas',
                       '_verificar_composicion','verificar_instalacion_red','_docker','bloqueo_historial','_validar', '_esperar_https'):
            parche=patch(prefijo+nombre); setattr(self,nombre,parche.start()); self.parches.append(parche)
        self.addCleanup(lambda: [p.stop() for p in reversed(self.parches)])
        self._bloqueo_instalacion.return_value = nullcontext()
        self.bloqueo_historial.return_value = nullcontext()
        self.leer_manifiesto_red.return_value = self.manifiesto
        self._verificar_composicion.return_value = estado()
        self.verificar_instalacion_red.return_value = estado()
        self._docker.return_value = b''

    def test_activa_no_reinicia_ni_carga_correo(self):
        self.assertEqual(reutilizar_red(self.carpeta), estado())
        self._docker.assert_not_called()
        self._validar.assert_not_called()
        self._esperar_https.assert_called_once()

    def test_detenida_arranca_backend_y_proxy_en_orden(self):
        self._verificar_composicion.side_effect = [estado(False,False),estado(False,False),estado(True,False),estado()]
        self.assertEqual(reutilizar_red(self.carpeta), estado())
        starts=[x.args[0] for x in self._docker.call_args_list if x.args[0][0]=='start']
        self.assertEqual(starts, [['start','1'*64],['start','2'*64]])
        self._validar.assert_called_once()

    def test_estado_mixto_no_altera_servicios(self):
        self._verificar_composicion.return_value = estado(True,False)
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self._docker.assert_not_called()

    def test_otros_contenedores_activos_no_inician(self):
        self._verificar_composicion.return_value = estado(False,False)
        self._docker.return_value = b'otro'
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self.assertEqual([x.args[0][0] for x in self._docker.call_args_list], ['ps'])

    def test_certificado_incorrecto_no_altera_servicios(self):
        self._validar_certificados.side_effect = ValueError('certificado')
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self._docker.assert_not_called()

    def test_id_distinto_no_altera_servicios(self):
        self._verificar_composicion.return_value = replace(estado(), backend=replace(estado().backend,identificador='3'*64))
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self._docker.assert_not_called()

    def test_manifest_cambiado_no_inicia(self):
        self.leer_manifiesto_red.side_effect = [self.manifiesto, self.manifiesto, None]
        self._verificar_composicion.return_value = estado(False,False)
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self.assertFalse(any(x.args[0][0]=='start' for x in self._docker.call_args_list))

    def test_fallo_backend_no_inicia_proxy_ni_borra(self):
        self._verificar_composicion.side_effect = [estado(False,False),estado(False,False),estado(False,False)]
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        comandos=[x.args[0][0] for x in self._docker.call_args_list]
        self.assertEqual(comandos.count('start'), 1)
        self.assertNotIn('rm', comandos)
        self.assertNotIn('stop', comandos)

    def test_respuesta_https_fallida_no_reinicia(self):
        self._esperar_https.side_effect = OSError('fallo')
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self._docker.assert_not_called()

    def test_marcador_pendiente_no_consulta_manifiesto(self):
        self._exigir_sin_actualizacion_pendiente.side_effect = ValueError('pendiente')
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        self.leer_manifiesto_red.assert_not_called()
        self._docker.assert_not_called()

    def test_error_no_revela_datos_del_error_original(self):
        self._validar_certificados.side_effect = ValueError('valor-privado-ficticio')
        with self.assertRaises(ErrorReutilizacionRed) as error: reutilizar_red(self.carpeta)
        self.assertNotIn('valor-privado-ficticio', str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_cambio_identidad_tras_backend_impide_iniciar_proxy(self):
        self._verificar_composicion.side_effect = [estado(False,False),estado(False,False),
            replace(estado(True,False), backend=replace(estado().backend,identificador='3'*64))]
        with self.assertRaises(ErrorReutilizacionRed): reutilizar_red(self.carpeta)
        starts=[x.args[0] for x in self._docker.call_args_list if x.args[0][0]=='start']
        self.assertEqual(starts, [['start','1'*64]])
