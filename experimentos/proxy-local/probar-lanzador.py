"""Prueba una entrada local con instalación ficticia; no usa Gmail."""
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import patch
import json
import os
import subprocess
import sys
import uuid

raiz = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(raiz))
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.iniciar_proxy_local_experimental import _crear_tls_interno, main
from taxiroute.instalacion_inicial import preparar_instalacion
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.reutilizacion_proxy_experimental import iniciar_y_registrar

proyecto = "taxiroute-lanzador-" + uuid.uuid4().hex
compose = ["docker", "compose", "-p", proyecto, "-f", str(raiz / "compose.proxy-local-experimental.yaml")]
temporal_raiz = Path(gettempdir()).resolve(strict=True)
with TemporaryDirectory(prefix="taxiroute-lanzador-", dir=temporal_raiz) as temporal:
    base = Path(temporal).resolve(strict=True)
    if base.parent != temporal_raiz or not base.name.startswith("taxiroute-lanzador-"):
        raise RuntimeError("Carpeta temporal inesperada.")
    instalacion = preparar_instalacion(base / "instalacion", usuario="AdminFicticio",
        nombre="Administración ficticia", correo="demo@example.invalid", contrasena="TemporalPrueba123!",
        confirmar_codigos=lambda codigos: len(codigos) == 5)
    identidad = instalacion.carpeta / "proxy-local"
    identidad.mkdir()
    certificado, clave = _crear_tls_interno(identidad / "tls-interno")
    rutas = {"datos": instalacion.datos, "cert_interno": certificado, "clave_interna": clave,
             "cert_externo": instalacion.certificado, "clave_externa": instalacion.clave}
    entorno = {**os.environ, "TAXIROUTE_PROXY_DATOS": str(instalacion.datos),
        "TAXIROUTE_CERT_INTERNO": str(certificado), "TAXIROUTE_CLAVE_INTERNA": str(clave),
        "TAXIROUTE_CERT_EXTERNO": str(instalacion.certificado), "TAXIROUTE_CLAVE_EXTERNA": str(instalacion.clave)}
    try:
        m = iniciar_y_registrar(identidad, proyecto=proyecto, rutas=rutas)
        _docker(["stop", "--time", "3", m.contenedores["proxy"]])
        _docker(["stop", "--time", "3", m.contenedores["backend"]])
        if main(["--carpeta", str(instalacion.carpeta), "--no-abrir"]) != 0:
            raise RuntimeError("No se reutilizó la instalación ficticia.")
        if main(["--carpeta", str(instalacion.carpeta), "--no-abrir"]) != 0:
            raise RuntimeError("No se conservó la instalación ya activa.")
        print("Lanzador: reapertura y repetición de instalación ficticia correctas.", flush=True)
        ficticio = json.dumps({"client_id": "cliente-ficticio", "client_secret": "clave-ficticia",
            "refresh_token": "token-ficticio", "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": [ALCANCE_ENVIO]})
        with patch("taxiroute.iniciar_proxy_local_experimental._almacen_windows") as almacen:
            almacen.return_value.get_password.return_value = ficticio
            if main(["--carpeta", str(instalacion.carpeta), "--conectar-gmail", "--no-abrir"]) != 0:
                raise RuntimeError("No se cargó la autorización ficticia desde el lanzador.")
            if almacen.return_value.get_password.call_count != 1:
                raise RuntimeError("La primera carga ficticia no ocurrió una vez.")
            _docker(["stop", "--time", "3", m.contenedores["proxy"]])
            _docker(["stop", "--time", "3", m.contenedores["backend"]])
            if main(["--carpeta", str(instalacion.carpeta), "--conectar-gmail", "--no-abrir"]) != 0:
                raise RuntimeError("No se recargó la autorización ficticia tras detener los servicios.")
            if almacen.return_value.get_password.call_count != 2:
                raise RuntimeError("La recarga ficticia no ocurrió una vez.")
        print("Lanzador: carga y recarga ficticias desde proveedor simulado correctas.", flush=True)
    finally:
        subprocess.run(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"],
                       cwd=raiz, env=entorno, check=True, capture_output=True)
