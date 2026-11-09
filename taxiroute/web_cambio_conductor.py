"""Cambio de contraseña de conductor para integrar explícitamente en la web.

La aplicación integradora debe restringir hosts, tamaño y frecuencia de peticiones
además de configurar HTTPS real. Este blueprint no inicia sesiones ni servicios.
"""
from flask import Blueprint, abort, jsonify, request

from taxiroute.acceso import _ahora, cambiar_contrasena, CambioContrasenaDenegado
from taxiroute.cuentas import DatosCuentaInvalidos


_MENSAJE_RECHAZO = "No se ha podido cambiar la contraseña. Revisa los datos y vuelve a intentarlo."


def crear_cambio_conductor(ruta, *, reloj=_ahora):
    """Crea la ruta de cambio; administradores deben usar su flujo con PIN."""
    cambio = Blueprint("cambio_conductor", __name__)

    @cambio.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @cambio.post("/acceso/conductor/cambio")
    def cambiar():
        if not request.is_secure:
            abort(400)
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        if request.args:
            abort(400)
        if not request.is_json:
            abort(415)
        datos = request.get_json(silent=True)
        claves = {"usuario", "contrasena_actual", "contrasena_nueva"}
        if not isinstance(datos, dict) or set(datos) != claves:
            return jsonify(error=_MENSAJE_RECHAZO), 400
        if (any(not isinstance(datos[clave], str) for clave in claves)
                or len(datos["usuario"]) > 40
                or len(datos["contrasena_actual"]) > 128
                or len(datos["contrasena_nueva"]) > 128):
            return jsonify(error=_MENSAJE_RECHAZO), 400
        try:
            cambiar_contrasena(ruta, datos["usuario"], datos["contrasena_actual"],
                               datos["contrasena_nueva"], reloj=reloj)
        except (CambioContrasenaDenegado, DatosCuentaInvalidos):
            # No distinguir cuenta, credencial, rol o incumplimiento de política.
            return jsonify(error=_MENSAJE_RECHAZO), 400
        except Exception:
            # No exponer detalles ni secretos de almacenamiento o hashing.
            return jsonify(error="No se ha podido confirmar el cambio. Prueba a iniciar sesión antes de reintentarlo."), 503
        return jsonify(paso="volver_a_iniciar")

    return cambio