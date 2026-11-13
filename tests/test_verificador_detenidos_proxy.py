"""La inspección de reutilización no reduce la protección de la carga."""
import unittest
import test_verificador_proxy_experimental as fixtures
from taxiroute.verificador_proxy_experimental import VerificacionProxyError, verificar_para_reutilizar


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
