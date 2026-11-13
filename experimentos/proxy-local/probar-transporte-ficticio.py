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

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.oauth_gmail import ALCANCE_ENVIO
from taxiroute.oauth_gmail_contenedor import _validar_contenido
from taxiroute.cargar_gmail_windows import CARGADOR, _estado_archivo
from taxiroute.verificador_proxy_experimental import VerificacionProxyError, verificar_composicion

spec = spec_from_file_location("ensayo_proxy", Path(__file__).with_name("probar-integracion.py"))
util = module_from_spec(spec)
spec.loader.exec_module(util)


def ejecutar(argumentos, *, entorno=None, entrada=None):
    return subprocess.run(argumentos, cwd=RAIZ, env=entorno, input=entrada,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def main():
    proyecto = "taxiroute-envio-" + uuid.uuid4().hex[:12]
    enviados = 0
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
            # La prueba obtiene las identidades de sus imágenes antes de crear
            # contenedores. Esto no acredita procedencia ni protege ante Docker hostil.
            ejecutar(compose + ["build"], entorno=entorno)
            imagenes = {}
            for servicio in ("backend", "proxy"):
                salida = ejecutar(["docker", "image", "inspect", f"{proyecto}-{servicio}",
                                   "--format", "{{.Id}}"])
                imagenes[servicio] = salida.decode("ascii").strip()
            ejecutar(compose + ["up", "-d", "--no-build"], entorno=entorno)
            opciones = dict(proyecto=proyecto, imagenes_esperadas=imagenes, rutas_esperadas=rutas)
            evidencia = verificar_composicion(**opciones)
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

            def transmitir_si_estable(anterior):
                nonlocal enviados
                # La misma función se utiliza tanto en el rechazo como en el éxito.
                verificar_composicion(**opciones, anterior=anterior)
                destino = anterior.backend.identificador
                if _estado_archivo(destino) != "ausente":
                    raise RuntimeError("El destino dejó de estar vacío.")
                # Tras intentar exec, un error no descarta una entrega parcial.
                try:
                    enviados += 1
                    ejecutar(["docker", "exec", "-i", "--user", "10001:10001", "--workdir",
                              "/app", destino, *CARGADOR], entrada=ficticio)
                    verificar_composicion(**opciones, anterior=anterior)
                    if _estado_archivo(destino) != "presente":
                        raise RuntimeError
                except Exception:
                    raise RuntimeError("No se confirmó la prueba; el envío pudo haberse realizado.") from None

            # Cambiar el proxy entre comprobaciones debe detener el envío.
            ejecutar(compose + ["up", "-d", "--no-deps", "--force-recreate", "proxy"], entorno=entorno)
            try:
                transmitir_si_estable(evidencia)
            except VerificacionProxyError:
                pass
            else:
                raise AssertionError("No se detectó la sustitución del proxy.")
            if enviados != 0 or _estado_archivo(backend) != "ausente":
                raise AssertionError("Se realizó un envío durante el rechazo.")
            print("Sustitución del proxy: rechazada antes del envío.")

            evidencia = verificar_composicion(**opciones)
            transmitir_si_estable(evidencia)
            if enviados != 1:
                raise AssertionError("Se esperaba exactamente un intento ficticio.")
            print("Transporte ficticio por stdin y archivo temporal: comprobados.")
        finally:
            subprocess.run(compose + ["down", "-v", "--remove-orphans", "--rmi", "local"],
                           cwd=RAIZ, env=entorno, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)


if __name__ == "__main__":
    main()
