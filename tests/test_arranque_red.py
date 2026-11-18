"""Pruebas aisladas del primer arranque LAN; Docker y credenciales no se usan."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from taxiroute.arranque_red import ErrorArranqueRed, arrancar_red


class PruebasArranqueRed(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="taxiroute-red-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.identidad = base / "identidad"
        self.identidad.mkdir()
        datos = base / "datos"
        datos.mkdir()
        for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
            (datos / nombre).write_text("ficticio", encoding="utf-8")
        self.opciones = dict(identidad=self.identidad, proyecto="red-prueba", direccion="192.168.1.25",
                             puerto=18773, datos=datos)
        for nombre in ("cert_interno", "clave_interna", "cert_externo", "clave_externa"):
            ruta = base / nombre
            ruta.write_text("ficticio", encoding="utf-8")
            self.opciones[nombre] = ruta
        evidencia = SimpleNamespace(backend=SimpleNamespace(identificador="a"*64),
                                   proxy=SimpleNamespace(identificador="b"*64))
        prefijo="taxiroute.arranque_red."
        for nombre in ("comprobar_direccion_local", "_validar_certificados", "verificar_certificado_externo", "_claves_distintas",
                       "verificar_permisos_windows", "_validar", "_sin_contenedores", "_compose",
                       "_esperar_https", "crear_manifiesto_red"):
            setattr(self, nombre, patch(prefijo+nombre).start())
        self.verificar_composicion_red = patch(prefijo+"verificar_composicion_red", return_value=evidencia).start()
        self.verificar_instalacion_red = patch(prefijo+"verificar_instalacion_red", return_value=evidencia).start()
        self.docker = patch(prefijo+"_docker", side_effect=self.respuesta_docker).start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def respuesta_docker(args):
        if args[0] == "ps":
            return b""
        return ("sha256:" + ("a" if "backend" in args[2] else "b")*64).encode()

    def test_verifica_y_registra_sin_cargar_gmail(self):
        arrancar_red(**self.opciones)
        self.assertEqual(self.comprobar_direccion_local.call_count, 2)
        self.assertEqual(self.verificar_composicion_red.call_count, 2)
        self.crear_manifiesto_red.assert_called_once()
        self.verificar_instalacion_red.assert_called_once_with(self.identidad)
        self.assertEqual(self._compose.call_args_list[1].args[0], ["up", "-d", "--no-build", "--no-recreate"])
        self.assertEqual(self._compose.call_args_list[1].args[2]["TAXIROUTE_RED_IP"], "192.168.1.25")
        self.assertTrue(all(c.args[0][0] in ("ps", "image") for c in self.docker.call_args_list))

    def test_si_la_ip_no_pertenece_al_host_no_crea_contenedores(self):
        self.comprobar_direccion_local.side_effect = ValueError("no disponible")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()

    def test_rechaza_contenedores_activos(self):
        self.docker.side_effect = lambda args: b"contenedor" if args[0] == "ps" else b""
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()

    def test_datos_invalidos_impiden_arranque(self):
        self._validar.side_effect = ValueError("datos")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()

    def test_certificado_incompatible_no_publica(self):
        self._validar_certificados.side_effect = ValueError("certificado")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()

    def test_error_https_no_registra_ni_detiene(self):
        self._esperar_https.side_effect = ErrorArranqueRed("fallo")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self.crear_manifiesto_red.assert_not_called()
        self.assertTrue(all(c.args[0][0] != "down" for c in self._compose.call_args_list))

    def test_identidad_preexistente_no_se_sobrescribe(self):
        (self.identidad / "manifiesto-proxy.json").write_text("conservar", encoding="utf-8")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()
        self.assertEqual((self.identidad / "manifiesto-proxy.json").read_text(), "conservar")

    def test_actualizacion_pendiente_impide_el_arranque(self):
        (self.identidad / ".actualizacion-pendiente").write_text("pendiente", encoding="utf-8")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._compose.assert_not_called()

    def test_si_el_dato_cambia_durante_construccion_no_publica(self):
        self._validar.side_effect = [None, ValueError("datos cambiados")]
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self.assertEqual([c.args[0] for c in self._compose.call_args_list], [["build"]])

    def test_fallo_de_permisos_no_accede_a_certificados(self):
        self.verificar_permisos_windows.side_effect = PermissionError("denegado")
        with self.assertRaises(ErrorArranqueRed): arrancar_red(**self.opciones)
        self._validar_certificados.assert_not_called()
        self._compose.assert_not_called()
