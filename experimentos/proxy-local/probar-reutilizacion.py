"""Ensayo temporal con credenciales ficticias; sin Gmail ni datos reales.

Con --reiniciar-desktop reinicia Docker Desktop solo si no hay otros
contenedores activos; ese reinicio afecta al servicio Docker del equipo.
No ejecutarlo mientras otra persona use Docker en este equipo.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import json
import http.client
import os
import ssl
import subprocess
import sys
import time
import uuid

raiz = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(raiz))
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.reutilizacion_proxy_experimental import iniciar_y_registrar, reutilizar
from taxiroute.cargar_gmail_windows import _docker

spec = spec_from_file_location("util", raiz / "experimentos/proxy-local/probar-integracion.py")
util = module_from_spec(spec)
spec.loader.exec_module(util)
proyecto = "taxiroute-reutilizacion-" + uuid.uuid4().hex
compose = ["docker", "compose", "-p", proyecto, "-f", str(util.COMPOSE)]
if sys.argv[1:] not in ([], ["--reiniciar-desktop"]):
    raise SystemExit("Opción no reconocida.")
reiniciar_desktop = bool(sys.argv[1:])

with TemporaryDirectory(prefix="taxiroute-reuso-") as temporal:
    base = Path(temporal)
    datos = preparar_demo_sqlite(base / "datos")
    carpeta = base / "identidad"
    carpeta.mkdir()
    ci, ki = util.certificado(base, "interno", dns="taxiroute-backend.local")
    ce, ke = util.certificado(base, "externo", dns=None)
    rutas = {"datos": datos, "cert_interno": ci, "clave_interna": ki,
             "cert_externo": ce, "clave_externa": ke}
    entorno = {**os.environ, "TAXIROUTE_PROXY_DATOS": str(datos), "TAXIROUTE_CERT_INTERNO": str(ci),
               "TAXIROUTE_CLAVE_INTERNA": str(ki), "TAXIROUTE_CERT_EXTERNO": str(ce),
               "TAXIROUTE_CLAVE_EXTERNA": str(ke)}
    ficticio = json.dumps({"client_id": "cliente-ficticio", "client_secret": "clave-ficticia",
        "refresh_token": "token-ficticio", "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": [ALCANCE_ENVIO]})
    proveedor = Mock(return_value=ficticio)
    try:
        m = iniciar_y_registrar(carpeta, proyecto=proyecto, rutas=rutas)
        primera = reutilizar(carpeta, proveedor=proveedor)
        if primera.correo != "cargado" or proveedor.call_count != 1:
            raise RuntimeError("Falló la carga ficticia inicial.")
        print("Inicio y carga ficticia: correctos.", flush=True)
        _docker(["stop", "--time", "3", m.contenedores["proxy"]])
        _docker(["stop", "--time", "3", m.contenedores["backend"]])
        segunda = reutilizar(carpeta, proveedor=proveedor)
        if segunda.correo != "cargado" or proveedor.call_count != 2:
            raise RuntimeError("Falló la recarga ficticia tras reinicio.")
        print("Reutilización ordenada y recarga ficticia tras detención: correctas.", flush=True)

        contexto = ssl.create_default_context(cafile=str(ce))
        origen = "https://127.0.0.1:18771"
        def solicitar(metodo, ruta, *, cuerpo=None, cookie=None):
            conexion = http.client.HTTPSConnection("127.0.0.1", 18771, context=contexto, timeout=5)
            cabeceras = {"Origin": origen, "Content-Type": "application/json"}
            if cookie:
                cabeceras["Cookie"] = cookie
            try:
                conexion.request(metodo, ruta, body=None if cuerpo is None else json.dumps(cuerpo), headers=cabeceras)
                respuesta = conexion.getresponse()
                contenido = respuesta.read()
                return respuesta.status, dict(respuesta.getheaders()), json.loads(contenido) if contenido else {}
            finally:
                conexion.close()

        def entrar():
            estado, headers, _ = solicitar("POST", "/acceso/conductor", cuerpo={
                "usuario": "AnaDemo", "contrasena": "ConductorDemo123!"})
            if estado != 200:
                raise RuntimeError("No se pudo acceder con la cuenta ficticia.")
            return headers["Set-Cookie"].split(";", 1)[0]

        cookie = entrar()
        estado, _, _ = solicitar("POST", "/api/carrera/iniciar", cookie=cookie,
            cuerpo={"revision": 0, "id_operacion": str(uuid.uuid4())})
        if estado != 200:
            raise RuntimeError("No se pudo iniciar la carrera ficticia.")
        time.sleep(6)
        _docker(["kill", "--signal", "KILL", m.contenedores["backend"]])
        tercera = reutilizar(carpeta, proveedor=proveedor)
        if tercera.correo != "cargado" or proveedor.call_count != 3:
            raise RuntimeError("Falló la recarga ficticia tras interrupción.")
        cookie = entrar()
        estado, _, vista = solicitar("GET", "/api/carrera/revision-recuperacion", cookie=cookie)
        punto = vista.get("pendiente")
        if estado != 200 or not isinstance(punto, dict):
            raise RuntimeError("La carrera ficticia no quedó pendiente de revisión.")
        estado, _, cierre = solicitar("POST", "/api/carrera/confirmar-recuperacion", cookie=cookie,
            cuerpo={"revision": punto["revision_recuperacion"], "ultimo_control": punto["ultimo_control"],
                    "huella": punto["huella"], "confirmar": True})
        if estado != 200:
            raise RuntimeError("No se pudo confirmar la revisión ficticia.")
        estado, _, historial = solicitar("GET", "/api/historial", cookie=cookie)
        carreras = historial.get("carreras", [])
        if (estado != 200 or len(carreras) != 1 or carreras[0]["fin"] != punto["ultimo_control"]
                or carreras[0]["importe"] != punto["importe_recuperado"]):
            raise RuntimeError("La carrera recuperada no coincide con el último punto fiable.")
        print("Carrera interrumpida: revisada y cerrada hasta el último punto fiable.", flush=True)

        estado, _, _ = solicitar("POST", "/api/carrera/iniciar", cookie=cookie,
            cuerpo={"revision": cierre["revision"], "id_operacion": str(uuid.uuid4())})
        if estado != 200:
            raise RuntimeError("No se pudo iniciar la segunda carrera ficticia.")
        time.sleep(6)
        _docker(["restart", "--time", "3", m.contenedores["backend"]])
        cuarta = reutilizar(carpeta, proveedor=proveedor)
        if cuarta.correo != "cargado" or proveedor.call_count != 4:
            raise RuntimeError("Falló la recarga ficticia tras reinicio del backend.")
        cookie = entrar()
        estado, _, historial = solicitar("GET", "/api/historial", cookie=cookie)
        if estado != 200 or len(historial.get("carreras", [])) != 1:
            raise RuntimeError("El historial ficticio no se conservó tras el reinicio.")
        estado, _, vista = solicitar("GET", "/api/carrera/revision-recuperacion", cookie=cookie)
        punto_segundo = vista.get("pendiente")
        if estado != 200 or not isinstance(punto_segundo, dict):
            raise RuntimeError("La segunda carrera ficticia no quedó pendiente de revisión.")
        estado, _, cierre_segundo = solicitar("POST", "/api/carrera/confirmar-recuperacion", cookie=cookie,
            cuerpo={"revision": punto_segundo["revision_recuperacion"], "ultimo_control": punto_segundo["ultimo_control"],
                    "huella": punto_segundo["huella"], "confirmar": True})
        if estado != 200:
            raise RuntimeError("No se pudo confirmar la segunda revisión ficticia.")
        estado, _, historial = solicitar("GET", "/api/historial", cookie=cookie)
        carreras = historial.get("carreras", [])
        if (estado != 200 or len(carreras) != 2
                or not any(c["fin"] == punto_segundo["ultimo_control"]
                           and c["importe"] == punto_segundo["importe_recuperado"] for c in carreras)):
            raise RuntimeError("La segunda carrera no coincide con el último punto fiable.")
        print("Recuperación de HTTPS, carrera y correo ficticios tras reinicio: correctas.", flush=True)

        if reiniciar_desktop:
            estado, _, _ = solicitar("POST", "/api/carrera/iniciar", cookie=cookie,
                cuerpo={"revision": cierre_segundo["revision"], "id_operacion": str(uuid.uuid4())})
            if estado != 200:
                raise RuntimeError("No se pudo iniciar la tercera carrera ficticia.")
            time.sleep(6)
            activos = set(_docker(["ps", "--no-trunc", "--format", "{{.ID}}"])
                          .decode("ascii").splitlines())
            if activos != set(m.contenedores.values()):
                raise RuntimeError("Hay otros contenedores activos; no se reiniciará Docker Desktop.")
            print("Reiniciando Docker Desktop con solo el ensayo ficticio activo...", flush=True)
            try:
                subprocess.run(["docker", "desktop", "restart", "--timeout", "180"],
                               cwd=raiz, check=True, capture_output=True, timeout=200)
            except Exception:
                subprocess.run(["docker", "desktop", "start", "--timeout", "180"],
                               cwd=raiz, check=False, capture_output=True, timeout=200)
                raise
            quinta = reutilizar(carpeta, proveedor=proveedor)
            if quinta.correo != "cargado" or proveedor.call_count != 5:
                raise RuntimeError("Falló la recarga ficticia tras reiniciar Docker Desktop.")
            cookie = entrar()
            estado, _, vista = solicitar("GET", "/api/carrera/revision-recuperacion", cookie=cookie)
            punto_tercero = vista.get("pendiente")
            if estado != 200 or not isinstance(punto_tercero, dict):
                raise RuntimeError("La carrera no quedó pendiente tras reiniciar Docker Desktop.")
            estado, _, _ = solicitar("POST", "/api/carrera/confirmar-recuperacion", cookie=cookie,
                cuerpo={"revision": punto_tercero["revision_recuperacion"],
                        "ultimo_control": punto_tercero["ultimo_control"],
                        "huella": punto_tercero["huella"], "confirmar": True})
            if estado != 200:
                raise RuntimeError("No se pudo confirmar la tercera revisión ficticia.")
            estado, _, historial = solicitar("GET", "/api/historial", cookie=cookie)
            carreras = historial.get("carreras", [])
            if (estado != 200 or len(carreras) != 3
                    or not any(c["fin"] == punto_tercero["ultimo_control"]
                               and c["importe"] == punto_tercero["importe_recuperado"] for c in carreras)):
                raise RuntimeError("La tercera carrera no coincide con el último punto fiable.")
            print("Docker Desktop reiniciado: carrera revisada, historial y carga ficticia recuperados.", flush=True)
    finally:
        subprocess.run(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"],
                       cwd=raiz, env=entorno, check=True, capture_output=True)
