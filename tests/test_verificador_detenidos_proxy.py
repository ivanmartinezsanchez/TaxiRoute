"""La inspección de reutilización no reduce la protección de la carga."""
import unittest
import test_verificador_proxy_experimental as fixtures
from taxiroute.verificador_proxy_experimental import VerificacionProxyError, verificar_para_reutilizar, _verificar_composicion


class PruebasDetenidos(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PruebasVerificador("test_acepta_configuracion_y_retorna_evidencia")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def revisar(self):
        return verificar_para_reutilizar(proyecto=self.f.proyecto, imagenes_esperadas=self.f.imagenes,
            rutas_esperadas=self.f.rutas, contenedores_esperados=self.f.ids)

    def test_detenidos_solo_se_admiten_en_inspeccion_de_reutilizacion(self):
        for item in self.f.contenedores.values():
            item["State"].update(Status="exited", Running=False)
        with self.assertRaises(VerificacionProxyError):
            self.f.verificar()
        estado = self.revisar()
        self.assertFalse(estado.backend.activo)
        self.assertFalse(estado.proxy.activo)

    def test_marcador_de_puerto_solo_admitido_con_los_dos_detenidos_y_modo_explicito(self):
        direccion, puerto = "192.168.1.25", 18773
        backend = self.f.contenedores["backend"]
        proxy = self.f.contenedores["proxy"]
        backend["Config"]["Cmd"] += ["--direccion", direccion, "--puerto-externo", str(puerto)]
        backend["HostConfig"]["PortBindings"] = {"18771/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]}
        for item in (backend, proxy):
            item["State"].update(Status="exited", Running=False)
        opciones = dict(proyecto=self.f.proyecto, imagenes_esperadas=self.f.imagenes,
            rutas_esperadas=self.f.rutas, direccion=direccion, puerto=puerto, permitir_detenidos=True)
        with self.assertRaises(VerificacionProxyError):
            _verificar_composicion(**opciones)
        _verificar_composicion(**opciones, puerto_no_observable_detenido=True)
        backend["State"].update(Status="running", Running=True)
        with self.assertRaises(VerificacionProxyError):
            _verificar_composicion(**opciones, puerto_no_observable_detenido=True)
        backend["HostConfig"]["PortBindings"] = {"18771/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(puerto)}]}
        with self.assertRaises(VerificacionProxyError):
            _verificar_composicion(**opciones, puerto_no_observable_detenido=True)
        backend["HostConfig"]["PortBindings"] = {"18771/tcp": [{"HostIp": direccion, "HostPort": str(puerto)}]}
        _verificar_composicion(**opciones, puerto_no_observable_detenido=True)

    def test_rechaza_estado_ambiguo_y_contenedor_sustituido(self):
        self.f.contenedores["backend"]["State"].update(Status="exited", Running=False, Paused=True)
        with self.assertRaises(VerificacionProxyError):
            self.revisar()
        self.f.contenedores["backend"]["State"].update(Status="exited", Running=False, Paused=False)
        esperados = dict(self.f.ids, proxy="f" * 64)
        with self.assertRaises(VerificacionProxyError):
            verificar_para_reutilizar(proyecto=self.f.proyecto, imagenes_esperadas=self.f.imagenes,
                rutas_esperadas=self.f.rutas, contenedores_esperados=esperados)


if __name__ == "__main__":
    unittest.main()
