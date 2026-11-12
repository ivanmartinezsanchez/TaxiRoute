"""Transferencia al contenedor con Docker y almacén simulados."""
import json
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

from taxiroute.cargar_gmail_windows import (
    COMANDO_SERVIDOR, ErrorCarga, IMAGEN, PROYECTO, SERVICIO, _verificar_contenedor, cargar,
)


class PruebasTransferenciaWindows(TestCase):
    def setUp(self):
        self.id = "a" * 64
        self.imagen = "sha256:" + "b" * 64
        self.info = {"Id": self.id, "Image": self.imagen, "State": {"Running": True},
            "Config": {"Image": IMAGEN, "User": "10001:10001", "WorkingDir": "/app", "Cmd": COMANDO_SERVIDOR,
                "Entrypoint": None, "Env": ["PATH=/usr/local/bin", "HOME=/home/taxiroute"],
                "Labels": {"com.docker.compose.project": PROYECTO, "com.docker.compose.service": SERVICIO}},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {"/run/taxiroute": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700"},
                "PortBindings": {"18770/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18770"}]}},
            "Mounts": [{"Destination": "/datos"}, {"Destination": "/run/secrets/tls_certificado"},
                       {"Destination": "/run/secrets/tls_clave"}]}

    def respuesta(self, argumentos, *, entrada=None):
        if argumentos[0] == "ps": return (self.id + "\n").encode()
        if argumentos[0] == "inspect": return json.dumps([self.info]).encode()
        if argumentos[:2] == ["image", "inspect"]: return json.dumps([{"Id": self.imagen, "Config": {"Entrypoint": None, "Env": ["PATH=/usr/local/bin", "HOME=/home/taxiroute"]}}]).encode()
        if argumentos[0] == "exec": return b""
        raise AssertionError("Comando inesperado")

    @patch("taxiroute.cargar_gmail_windows._docker")
    def test_verifica_destino(self, docker):
        docker.side_effect = self.respuesta
        self.assertEqual(_verificar_contenedor(), self.id)
        self.assertEqual(docker.call_args.args[0][0], "exec")

    @patch("taxiroute.cargar_gmail_windows._almacen_windows")
    @patch("taxiroute.cargar_gmail_windows._docker")
    def test_destino_invalido_no_lee_el_almacen(self, docker, almacen):
        self.info["HostConfig"]["PortBindings"]["18770/tcp"][0]["HostIp"] = "0.0.0.0"
        docker.side_effect = self.respuesta
        with self.assertRaises(ErrorCarga): cargar()
        almacen.assert_not_called()

    @patch("taxiroute.cargar_gmail_windows._docker")
    def test_rechaza_configuraciones_alteradas(self, docker):
        original = deepcopy(self.info)
        variantes = [
            lambda i: i["Config"].update(User="root"),
            lambda i: i["Config"].update(Cmd=["otro"]),
            lambda i: i["Config"]["Env"].append("PYTHONPATH=/datos"),
            lambda i: i["Config"]["Env"].append("LD_PRELOAD=/datos/malicioso.so"),
            lambda i: i["Config"].update(Entrypoint=["otro"]),
            lambda i: i["HostConfig"].update(ReadonlyRootfs=False),
            lambda i: i["HostConfig"].update(Tmpfs={}),
            lambda i: i["HostConfig"]["Tmpfs"].update({"/run/taxiroute": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,mode=0777"}),
            lambda i: i["Mounts"].append({"Destination":"/app"}),
            lambda i: i.update(Image="sha256:" + "c" * 64),
        ]
        for alterar in variantes:
            with self.subTest(alterar=alterar):
                self.info = deepcopy(original); alterar(self.info); docker.side_effect = self.respuesta
                with self.assertRaises(ErrorCarga): _verificar_contenedor()

    @patch("taxiroute.cargar_gmail_windows._almacen_windows")
    @patch("taxiroute.cargar_gmail_windows._docker")
    def test_transmite_solo_por_entrada_estandar(self, docker, almacen):
        docker.side_effect = self.respuesta
        almacen.return_value.get_password.return_value = "credencial-ficticia"
        cargar()
        transferencias = [llamada for llamada in docker.call_args_list if "entrada" in llamada.kwargs]
        self.assertEqual(len(transferencias), 1)
        args, kwargs = transferencias[0]
        self.assertEqual(kwargs["entrada"], b"credencial-ficticia")
        self.assertIn(self.id, args[0])
        self.assertNotIn("credencial-ficticia", " ".join(args[0]))
        self.assertEqual(docker.call_args.args[0][0], "exec")

    @patch("taxiroute.cargar_gmail_windows._almacen_windows")
    @patch("taxiroute.cargar_gmail_windows._verificar_contenedor", return_value="a" * 64)
    @patch("taxiroute.cargar_gmail_windows._docker")
    def test_si_falla_verificacion_posterior_no_informa_exito(self, docker, verificar, almacen):
        almacen.return_value.get_password.return_value = "credencial-ficticia"
        docker.side_effect = [b"", RuntimeError("detalle privado")]
        with self.assertRaises(ErrorCarga) as fallo:
            cargar()
        self.assertNotIn("detalle privado", str(fallo.exception))

    @patch("taxiroute.cargar_gmail_windows._almacen_windows")
    @patch("taxiroute.cargar_gmail_windows._verificar_contenedor", return_value="a" * 64)
    @patch("taxiroute.cargar_gmail_windows._docker", side_effect=RuntimeError("credencial-ficticia"))
    def test_error_no_expone_detalles(self, docker, verificar, almacen):
        almacen.return_value.get_password.return_value = "credencial-ficticia"
        with self.assertRaises(ErrorCarga) as fallo: cargar()
        self.assertNotIn("credencial-ficticia", str(fallo.exception))
        self.assertIsNone(fallo.exception.__cause__)


if __name__ == "__main__":
    import unittest
    unittest.main()
