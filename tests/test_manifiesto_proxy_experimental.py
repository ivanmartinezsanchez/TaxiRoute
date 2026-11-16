from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import hashlib
import unittest

from taxiroute.manifiesto_proxy_experimental import (ARCHIVO, MARCADOR, ErrorManifiesto,
    crear_manifiesto, leer_manifiesto, crear_manifiesto_red, leer_manifiesto_red)


class PruebasManifiesto(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix="taxiroute-identidad-")
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name).resolve()
        self.carpeta = self.raiz / "instalacion"
        self.carpeta.mkdir()
        datos = self.raiz / "datos"
        datos.mkdir()
        self.rutas = {"datos": datos}
        for nombre in ("cert_interno", "clave_interna", "cert_externo", "clave_externa"):
            self.rutas[nombre] = self.raiz / nombre
            self.rutas[nombre].write_text("prueba-" + nombre, encoding="utf-8")
        self.imagenes = {"backend": "sha256:" + "a" * 64, "proxy": "sha256:" + "b" * 64}
        self.contenedores = {"backend": "1" * 64, "proxy": "2" * 64}

    def crear(self):
        return crear_manifiesto(self.carpeta, proyecto="taxi-ficticio", imagenes=self.imagenes,
                                contenedores=self.contenedores, rutas=self.rutas)

    def test_creacion_y_relectura_no_incluyen_claves(self):
        resultado = self.crear()
        self.assertEqual(leer_manifiesto(self.carpeta), resultado)
        contenido = (self.carpeta / ARCHIVO).read_text(encoding="utf-8")
        self.assertNotIn("prueba-clave_interna", contenido)
        self.assertNotIn("prueba-clave_externa", contenido)
        self.assertEqual(asdict(resultado)["version"], 1)

    def test_no_sobrescribe_y_conserva_parcial(self):
        ruta = self.carpeta / ARCHIVO
        ruta.write_text("parcial", encoding="utf-8")
        with self.assertRaises(ErrorManifiesto):
            self.crear()
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto(self.carpeta)
        self.assertEqual(ruta.read_text(encoding="utf-8"), "parcial")

    def test_detecta_certificado_cambiado_y_ruta_perdida(self):
        self.crear()
        self.rutas["cert_interno"].write_text("otro", encoding="utf-8")
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto(self.carpeta)

    def test_rechaza_marcador_ausente_y_alterado(self):
        self.crear()
        marcador = self.carpeta / MARCADOR
        marcador.unlink()
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto(self.carpeta)
        marcador.write_text("parcial", encoding="ascii")
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto(self.carpeta)

    def test_rechaza_json_con_claves_duplicadas_y_campo_extra(self):
        self.crear()
        ruta = self.carpeta / ARCHIVO
        for contenido in ('{"version":1,"version":1}', json.dumps({"version": 1, "adicional": True})):
            ruta.write_text(contenido, encoding="utf-8")
            (self.carpeta / MARCADOR).write_text(hashlib.sha256(contenido.encode()).hexdigest(), encoding="ascii")
            with self.assertRaises(ErrorManifiesto):
                leer_manifiesto(self.carpeta)

    def test_rechaza_imagenes_contenedores_y_rutas_repetidas(self):
        self.contenedores["proxy"] = self.contenedores["backend"]
        with self.assertRaises(ErrorManifiesto):
            self.crear()
        self.assertFalse((self.carpeta / ARCHIVO).exists())


    def crear_red(self, direccion="192.168.1.25", puerto=18773):
        return crear_manifiesto_red(self.carpeta, proyecto="taxi-ficticio", imagenes=self.imagenes,
            contenedores=self.contenedores, rutas=self.rutas, direccion=direccion, puerto=puerto)

    def test_red_crea_y_relee_con_autoridad_explicita(self):
        resultado = self.crear_red()
        self.assertEqual(leer_manifiesto_red(self.carpeta), resultado)
        self.assertEqual((resultado.version, resultado.direccion, resultado.puerto), (2, "192.168.1.25", 18773))
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto(self.carpeta)

    def test_lector_red_rechaza_v1(self):
        self.crear()
        with self.assertRaises(ErrorManifiesto):
            leer_manifiesto_red(self.carpeta)

    def test_rechaza_direcciones_y_puertos_no_admisibles(self):
        for ip in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "169.254.1.1", "::1", "192.168.001.1", None):
            with self.subTest(ip=ip), self.assertRaises(ErrorManifiesto):
                self.crear_red(direccion=ip)
        for puerto in (0, 65536, -1, True, "18773", None):
            with self.subTest(puerto=puerto), self.assertRaises(ErrorManifiesto):
                self.crear_red(puerto=puerto)
        self.assertFalse((self.carpeta / ARCHIVO).exists())

    def test_rechaza_campo_extra_y_autoridad_invalida_al_leer(self):
        self.crear_red()
        ruta = self.carpeta / ARCHIVO
        original = json.loads(ruta.read_text(encoding="utf-8"))
        for cambios in ({"extra": True}, {"direccion": "8.8.8.8"}, {"puerto": True}, {"version": 1}):
            with self.subTest(cambios=cambios):
                contenido = json.dumps({**original, **cambios}, ensure_ascii=False).encode("utf-8")
                ruta.write_bytes(contenido)
                (self.carpeta / MARCADOR).write_text(hashlib.sha256(contenido).hexdigest(), encoding="ascii")
                with self.assertRaises(ErrorManifiesto):
                    leer_manifiesto_red(self.carpeta)

    def test_red_no_sobrescribe_manifiesto_existente(self):
        self.crear()
        antes = (self.carpeta / ARCHIVO).read_bytes()
        with self.assertRaises(ErrorManifiesto):
            self.crear_red()
        self.assertEqual((self.carpeta / ARCHIVO).read_bytes(), antes)


if __name__ == "__main__":
    unittest.main()
