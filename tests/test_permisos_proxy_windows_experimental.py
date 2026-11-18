"""Pruebas portables de la comprobación conservadora de permisos Windows."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import subprocess
import unittest

from taxiroute import permisos_proxy_windows_experimental as acl


class PruebasPermisosProxy(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix="acl-proxy-ficticio-")
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name).resolve()
        self.instalacion = self.base / "instalacion"
        self.instalacion.mkdir()
        (self.instalacion / "datos").mkdir()
        (self.instalacion / "datos" / "archivo").write_text("ficticio", encoding="utf-8")
        self.usuario = "S-1-5-21-1-2-3-1001"
        self.permitidos = [self.usuario, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"]
        parche = patch.object(acl, "_es_windows", return_value=True)
        parche.start()
        self.addCleanup(parche.stop)

    def respuesta(self, rutas):
        return {"usuario": self.usuario, "archivos": [
            {"propietario": self.usuario, "permitidos": list(self.permitidos)} for _ in rutas]}

    def test_admite_propietario_y_grupos_previstos_incluido_owner_rights(self):
        with patch.object(acl, "_consultar", side_effect=self.respuesta) as consultar:
            self.assertIsNone(acl.verificar_permisos_windows(self.instalacion))
        rutas = consultar.call_args.args[0]
        self.assertEqual(set(rutas), {self.base, self.instalacion,
                                     self.instalacion / "datos", self.instalacion / "datos" / "archivo"})

    def test_padre_de_nueva_instalacion_se_puede_comprobar_sin_crearla(self):
        destino = self.base / "aun-no-existe"
        with patch.object(acl, "_consultar", side_effect=self.respuesta) as consultar:
            acl.verificar_permisos_windows(destino, permitir_ausente=True)
        self.assertEqual(consultar.call_args.args[0], [self.base])
        self.assertFalse(destino.exists())
        with self.assertRaises(acl.PermisosProxyError):
            acl.verificar_permisos_windows(destino)

    def test_rechaza_otro_usuario_propietario_grupo_desconocido_o_faltantes(self):
        def datos(rutas, caso):
            valor = self.respuesta(rutas)
            if caso == "dueno": valor["archivos"][0]["propietario"] = "S-1-5-21-9-9-9-999"
            if caso == "grupo": valor["archivos"][0]["permitidos"].append("S-1-1-0")
            if caso == "vacio": valor["archivos"][0]["permitidos"] = []
            if caso == "faltantes": valor["archivos"].pop()
            if caso == "usuario": valor["usuario"] = "S-1-3-4"
            return valor
        for caso in ("dueno", "grupo", "vacio", "faltantes", "usuario"):
            with self.subTest(caso=caso), patch.object(acl, "_consultar", side_effect=lambda rutas: datos(rutas, caso)):
                with self.assertRaises(acl.PermisosProxyError):
                    acl.verificar_permisos_windows(self.instalacion)

    def test_rechaza_arbol_grande_sin_consultar_acl(self):
        with patch.object(acl, "_MAX_RUTAS", 2), patch.object(acl, "_consultar") as consultar:
            with self.assertRaises(acl.PermisosProxyError):
                acl.verificar_permisos_windows(self.instalacion)
            consultar.assert_not_called()

    def test_error_de_consulta_no_revela_ruta(self):
        with patch.object(acl, "_consultar", side_effect=ValueError("ruta privada de prueba")):
            with self.assertRaises(acl.PermisosProxyError) as error:
                acl.verificar_permisos_windows(self.instalacion)
        self.assertNotIn("ruta privada", str(error.exception))

    def test_rechaza_enlace_sin_consultar_acl(self):
        enlace = self.instalacion / "enlace"
        try:
            enlace.symlink_to(self.instalacion / "datos", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("El sistema no permite crear enlaces de prueba.")
        with patch.object(acl, "_consultar") as consultar:
            with self.assertRaises(acl.PermisosProxyError):
                acl.verificar_permisos_windows(self.instalacion)
            consultar.assert_not_called()

    def test_comando_no_interpela_la_ruta_ni_usa_shell(self):
        ruta = self.base / "nombre con espacios; $(texto)"
        resultado = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.respuesta([ruta])).encode(), stderr=b"")
        with patch.dict(os.environ, {"SystemRoot": str(self.base)}), patch.object(acl.subprocess, "run", return_value=resultado) as ejecutar:
            self.assertEqual(acl._consultar([ruta]), self.respuesta([ruta]))
        llamada = ejecutar.call_args
        self.assertNotIn(str(ruta), " ".join(llamada.args[0]))
        self.assertEqual(json.loads(llamada.kwargs["env"]["TAXIROUTE_ACL_PATHS"]), [str(ruta)])
        self.assertNotIn("shell", llamada.kwargs)
        self.assertIn("-EncodedCommand", llamada.args[0])


if __name__ == "__main__":
    unittest.main()
