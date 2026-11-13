"""Ensayo de transporte OAuth ficticio en Docker; no usa Gmail ni el almacén.

Requiere Docker, dependencias instaladas y localhost:18771 libre. El JSON se
construye en memoria con cadenas sin validez. Usa proyecto temporal exclusivo.
La inspección no es atómica y esta prueba no autoriza transmitir secretos reales.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import uuid
from unittest.mock import Mock, patch

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.oauth_gmail_contenedor import _validar_contenido
from taxiroute.cargar_gmail_windows import _docker, _estado_archivo
from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, cargar_proxy_experimental
from taxiroute.arranque_proxy_experimental import arrancar_proxy
from taxiroute.verificador_proxy_experimental import verificar_composicion

spec = spec_from_file_location("ensayo_proxy", Path(__file__).with_name("probar-integracion.py"))
util = module_from_spec(spec)
spec.loader.exec_module(util)


def ejecutar(argumentos, *, entorno=None, entrada=None):
    return subprocess.run(argumentos, cwd=RAIZ, env=entorno, input=entrada,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def main():
    proyecto = "taxiroute-envio-" + uuid.uuid4().hex
    # No se limpia un proyecto que existiera antes de este ensayo.
    for tipo, formato in (("ps", "{{.ID}}"), ("network", "{{.ID}}"), ("volume", "{{.Name}}")):
        comando = (["docker", "ps", "-a"] if tipo == "ps"
                   else ["docker", tipo, "ls"])
        salida = ejecutar(comando + ["--filter", "label=com.docker.compose.project=" + proyecto,
                                    "--format", formato])
        if salida.strip():
            raise RuntimeError("Ya existen recursos con el nombre del ensayo.")
    with TemporaryDirectory(prefix="taxiroute-oauth-ficticio-") as temporal:
        base = Path(temporal)
        datos = preparar_demo_sqlite(base / "datos")
        interno, clave_i = util.certificado(base, "interno", dns="taxiroute-backend.local")
        externo, clave_e = util.certificado(base, "externo", dns=None)
        rutas = {"datos": datos, "cert_interno": interno, "clave_interna": clave_i,
                 "cert_externo": externo, "clave_externa": clave_e}
        entorno = {**os.environ, "TAXIROUTE_PROXY_DATOS": str(datos),
                   "TAXIROUTE_CERT_INTERNO": str(interno), "TAXIROUTE_CLAVE_INTERNA": str(clave_i),
                   "TAXIROUTE_CERT_EXTERNO": str(externo), "TAXIROUTE_CLAVE_EXTERNA": str(clave_e)}
        compose = ["docker", "compose", "-p", proyecto, "-f", str(util.COMPOSE)]
        try:
            # El arranque comprueba TLS y captura IDs antes de crear servicios.
            # Los IDs no acreditan procedencia ni protegen frente a Docker hostil.
            arranque = arrancar_proxy(proyecto=proyecto, **rutas)
            imagenes = {"backend": arranque.imagen_backend, "proxy": arranque.imagen_proxy}
            opciones = dict(proyecto=proyecto, imagenes_esperadas=imagenes, rutas_esperadas=rutas)
            evidencia = arranque.evidencia
            print("Primer arranque ficticio con HTTPS e identidades: comprobado.")
            backend = evidencia.backend.identificador
            if _estado_archivo(backend) != "ausente":
                raise RuntimeError("La instalación de prueba no empezó vacía.")

            # No se consulta el almacén ni se solicita un PIN. Estos valores no
            # son credenciales y nunca se imprimen.
            ficticio = json.dumps({"client_id": "cliente-ficticio-sin-validez",
                "client_secret": "clave-ficticia-sin-validez",
                "refresh_token": "token-ficticio-sin-validez",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [ALCANCE_ENVIO]}).encode("utf-8")
            _validar_contenido(ficticio)

            proveedor = Mock(return_value=ficticio.decode("utf-8"))
            transporte = Mock(side_effect=_docker)

            # Ambos recorridos invocan la misma función experimental de carga.
            with patch("taxiroute.carga_proxy_experimental._docker", transporte):
                ejecutar(compose + ["up", "-d", "--no-deps", "--force-recreate", "proxy"], entorno=entorno)
                try:
                    cargar_proxy_experimental(**opciones, evidencia_esperada=evidencia, proveedor=proveedor)
                except CargaProxyAntesEnvio:
                    pass
                else:
                    raise AssertionError("No se detectó la sustitución del proxy.")
                proveedor.assert_not_called()
                transporte.assert_not_called()
                if _estado_archivo(backend) != "ausente":
                    raise AssertionError("La prueba rechazada modificó el archivo temporal.")
                print("Sustitución del proxy: rechazada antes de leer o enviar.")

                evidencia = verificar_composicion(**opciones)
                resultado = cargar_proxy_experimental(
                    **opciones, evidencia_esperada=evidencia, proveedor=proveedor)
                if resultado != evidencia:
                    raise AssertionError("La evidencia cambió tras el envío.")
                proveedor.assert_called_once_with()
                transporte.assert_called_once()
                if _estado_archivo(backend) != "presente":
                    raise AssertionError("No se confirmó el archivo ficticio.")
            print("Transporte ficticio por stdin y archivo temporal: comprobados.")
        finally:
            subprocess.run(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"],
                           cwd=RAIZ, env=entorno, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)


if __name__ == "__main__":
    main()
