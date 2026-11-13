"""Pruebas de verificación del proxy con inspecciones Docker ficticias."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from taxiroute.verificador_proxy_experimental import (
    COMANDO_BACKEND, VerificacionProxyError, verificar_composicion,
)


class PruebasVerificador(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix="proxy-verificacion-")
        self.addCleanup(temporal.cleanup)
        raiz = Path(temporal.name).resolve()
        datos = raiz / "datos"
        datos.mkdir()
        self.rutas = {"datos": datos}
        for nombre in ("cert_interno", "clave_interna", "cert_externo", "clave_externa"):
            archivo = raiz / nombre
            archivo.write_text("ficticio", encoding="utf-8")
            self.rutas[nombre] = archivo
        self.proyecto = "taxiroute-prueba"
        self.ids = {"backend": "1" * 64, "proxy": "2" * 64}
        self.imagenes = {"backend": "sha256:" + "a" * 64, "proxy": "sha256:" + "b" * 64}
        self.env = ["PATH=/ficticio"]
        self.contenedores = {}
        for servicio in ("backend", "proxy"):
            proxy = servicio == "proxy"
            etiquetas = {"com.docker.compose.project": self.proyecto,
                "com.docker.compose.service": servicio, "com.docker.compose.container-number": "1",
                "com.docker.compose.oneoff": "False"}
            config = {"Labels": etiquetas, "Image": f"{self.proyecto}-{servicio}", "Env": self.env[:],
                "User": "101:101" if proxy else "10001:10001", "WorkingDir": "/" if proxy else "/app",
                "Entrypoint": ["nginx"] if proxy else None,
                "Cmd": ["-g", "daemon off;", "-c", "/etc/nginx/nginx.conf"] if proxy else COMANDO_BACKEND[:]}
            host = {"ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
                "SecurityOpt": ["no-new-privileges:true"], "Devices": None, "DeviceRequests": None,
                "VolumesFrom": None, "GroupAdd": None, "PidMode": "", "IpcMode": "private", "UTSMode": "",
                "NetworkMode": "container:" + self.ids["backend"] if proxy else self.proyecto + "_default",
                "PortBindings": {} if proxy else {"18771/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18771"}]},
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,uid=101,gid=101,mode=0700"} if proxy else
                    {"/tmp": "", "/run/taxiroute": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700"}}
            destinos = ({"/cert/interno.pem": "cert_interno", "/cert/externo.pem": "cert_externo",
                         "/cert/externo.key": "clave_externa"} if proxy else
                        {"/datos": "datos", "/cert/interno.pem": "cert_interno",
                         "/cert/interno.key": "clave_interna"})
            montajes = [{"Destination": d, "Source": str(self.rutas[r]), "Type": "bind", "RW": r == "datos"}
                        for d, r in destinos.items()]
            self.contenedores[servicio] = {"Id": self.ids[servicio], "Image": self.imagenes[servicio],
                "RestartCount": 0, "State": {"Status": "running", "Running": True, "Paused": False,
                    "Restarting": False, "Dead": False, "StartedAt": "2026-11-13T12:00:00Z"},
                "Config": config, "HostConfig": host, "Mounts": montajes,
                "NetworkSettings": {"Networks": {} if proxy else {self.proyecto + "_default": {}}}}
        self.llamadas = []
        parche = patch("taxiroute.verificador_proxy_experimental._docker", side_effect=self.docker)
        parche.start()
        self.addCleanup(parche.stop)

    def docker(self, argumentos):
        self.llamadas.append(argumentos)
        if argumentos[0] == "ps":
            if "service=backend" in argumentos[-3]:
                servicio = "backend"
            elif "service=proxy" in argumentos[-3]:
                servicio = "proxy"
            else:
                raise AssertionError(argumentos)
            return (self.ids[servicio] + "\n").encode()
        if argumentos[0] == "inspect":
            servicio = next(k for k, v in self.ids.items() if v == argumentos[1])
            return json.dumps([self.contenedores[servicio]]).encode()
        if argumentos[:2] == ["image", "inspect"]:
            return json.dumps([{"Id": argumentos[2], "Config": {"Env": self.env}}]).encode()
        raise AssertionError(argumentos)

    def verificar(self, **opciones):
        return verificar_composicion(proyecto=self.proyecto, imagenes_esperadas=self.imagenes,
                                    rutas_esperadas=self.rutas, **opciones)

    def test_acepta_configuracion_y_retorna_evidencia(self):
        evidencia = self.verificar()
        self.assertEqual(evidencia.backend.identificador, self.ids["backend"])
        self.assertEqual(evidencia.proxy.imagen, self.imagenes["proxy"])
        self.assertEqual(evidencia.backend.reinicios, 0)
        self.assertEqual(self.verificar(anterior=evidencia), evidencia)
        self.assertTrue(all(c[0] in ("ps", "inspect", "image") for c in self.llamadas))

    def test_rechaza_alteraciones_de_seguridad(self):
        casos = [
            ("imagen", lambda d: d["proxy"].update(Image="sha256:"+"f"*64)),
            ("etiqueta", lambda d: d["proxy"]["Config"]["Labels"].update({"com.docker.compose.service":"backend"})),
            ("reiniciando", lambda d: d["proxy"]["State"].update(Restarting=True)),
            ("namespace", lambda d: d["proxy"]["HostConfig"].update(NetworkMode="container:"+"f"*64)),
            ("puerto", lambda d: d["backend"]["HostConfig"].update(PortBindings={"18771/tcp":[{"HostIp":"0.0.0.0","HostPort":"18771"}]})),
            ("capacidad", lambda d: d["proxy"]["HostConfig"].update(CapAdd=["SYS_ADMIN"])),
            ("sin_raiz_ro", lambda d: d["proxy"]["HostConfig"].update(ReadonlyRootfs=False)),
            ("entrada", lambda d: d["proxy"]["Config"].update(Entrypoint=["sh"])),
            ("entorno", lambda d: d["backend"]["Config"]["Env"].append("INYECCION=1")),
            ("tmpfs", lambda d: d["backend"]["HostConfig"]["Tmpfs"].update({"/run/taxiroute":"rw"})),
            ("montaje_rw", lambda d: d["proxy"]["Mounts"][0].update(RW=True)),
            ("montaje_extra", lambda d: d["proxy"]["Mounts"].append(deepcopy(d["proxy"]["Mounts"][0]))),
            ("red_adicional", lambda d: d["backend"]["NetworkSettings"]["Networks"].update(otra={})),
        ]
        original = deepcopy(self.contenedores)
        for nombre, alterar in casos:
            with self.subTest(nombre=nombre):
                self.contenedores = deepcopy(original)
                alterar(self.contenedores)
                with self.assertRaises(VerificacionProxyError):
                    self.verificar()

    def test_rechaza_reinicio_mismo_id_y_cambio_de_arranque(self):
        anterior = self.verificar()
        self.contenedores["backend"]["RestartCount"] += 1
        with self.assertRaises(VerificacionProxyError):
            self.verificar(anterior=anterior)
        self.contenedores["backend"]["RestartCount"] = 0
        self.contenedores["backend"]["State"]["StartedAt"] = "2026-11-13T13:00:00Z"
        with self.assertRaises(VerificacionProxyError):
            self.verificar(anterior=anterior)

    def test_rechaza_proxy_ausente_o_duplicado(self):
        original = self.docker
        for salida in (b"", (self.ids["proxy"] + "\n" + "3" * 64 + "\n").encode()):
            with self.subTest(salida=salida):
                def respuesta(argumentos):
                    if argumentos[0] == "ps" and "service=proxy" in argumentos[-3]:
                        return salida
                    return original(argumentos)
                with patch("taxiroute.verificador_proxy_experimental._docker", side_effect=respuesta):
                    with self.assertRaises(VerificacionProxyError):
                        self.verificar()

    def test_rechaza_sustitucion_con_imagen_y_etiqueta_distintas(self):
        proxy_nuevo = "3" * 64
        self.ids["proxy"] = proxy_nuevo
        proxy = self.contenedores["proxy"]
        proxy["Id"] = proxy_nuevo
        proxy["Image"] = "sha256:" + "c" * 64
        proxy["Config"]["Image"] = self.proyecto + "-otra-imagen"
        with self.assertRaises(VerificacionProxyError):
            self.verificar()

    def test_rechaza_reinicio_o_arranque_distinto_del_proxy(self):
        anterior = self.verificar()
        self.contenedores["proxy"]["RestartCount"] += 1
        with self.assertRaises(VerificacionProxyError):
            self.verificar(anterior=anterior)
        self.contenedores["proxy"]["RestartCount"] = 0
        self.contenedores["proxy"]["State"]["StartedAt"] = "2026-11-13T13:00:00Z"
        with self.assertRaises(VerificacionProxyError):
            self.verificar(anterior=anterior)

    def test_exige_imagenes_y_rutas_de_confianza(self):
        self.imagenes["backend"] = "backend:latest"
        with self.assertRaises(VerificacionProxyError):
            self.verificar()
        self.imagenes["backend"] = "sha256:" + "a" * 64
        self.rutas.pop("clave_externa")
        with self.assertRaises(VerificacionProxyError):
            self.verificar()
        self.assertEqual(self.llamadas, [])


if __name__ == "__main__":
    unittest.main()
