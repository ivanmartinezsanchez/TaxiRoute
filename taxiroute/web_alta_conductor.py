"""Ruta experimental de alta; no se registra en la web por sí sola."""
import sqlite3

from argon2.exceptions import HashingError
from flask import Blueprint, abort, jsonify, request

from taxiroute.alta_conductor import AltaDenegada, alta_conductor
from taxiroute.cuentas import DatosCuentaInvalidos, UsuarioDuplicado
from taxiroute.restablecimiento_conductor import RestablecimientoDenegado, restablecer_contrasena_conductor
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


def crear_alta_conductor_web(sesiones):
    """La aplicación integradora debe restringir hosts, tamaño y configurar TLS."""
    api = Blueprint("alta_conductor_web", __name__, url_prefix="/api/administracion")

    @api.before_request
    def comprobar_solicitud():
        if not request.is_secure:
            abort(400)
        if request.args:
            abort(400)
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        if not request.is_json:
            abort(415)

    @api.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @api.post("/conductores")
    @requerir_sesion(sesiones, roles={"administrador"})
    def crear():
        datos = request.get_json(silent=True)
        if (not isinstance(datos, dict)
                or set(datos) != {"usuario", "nombre_visible", "contrasena"}
                or any(not isinstance(valor, str) for valor in datos.values())):
            abort(400)
        try:
            cuenta = alta_conductor(sesiones.ruta, request.cookies.get(NOMBRE_COOKIE),
                nombre_usuario=datos["usuario"], nombre_visible=datos["nombre_visible"],
                contrasena=datos["contrasena"], reloj=sesiones._reloj)
        except AltaDenegada:
            return jsonify(error="No se ha podido realizar el alta."), 403
        except UsuarioDuplicado:
            return jsonify(error="No se ha podido crear la cuenta: el usuario ya existe."), 409
        except DatosCuentaInvalidos as error:
            return jsonify(error=str(error)), 400
        except (sqlite3.Error, OSError, HashingError):
            return jsonify(error="No se puede confirmar el alta. Actualiza la lista antes de reintentar."), 503
        return jsonify(id=cuenta.id, usuario=cuenta.nombre_usuario,
                       nombre_visible=cuenta.nombre_visible, cambio_obligatorio=True), 201

    @api.post("/conductores/<usuario_id>/restablecer-contrasena")
    @requerir_sesion(sesiones, roles={"administrador"})
    def restablecer(usuario_id):
        datos = request.get_json(silent=True)
        if (not isinstance(datos, dict) or set(datos) != {"contrasena"}
                or not isinstance(datos["contrasena"], str)):
            abort(400)
        try:
            restablecer_contrasena_conductor(sesiones.ruta, request.cookies.get(NOMBRE_COOKIE),
                usuario_id, datos["contrasena"], reloj=sesiones._reloj)
        except RestablecimientoDenegado:
            return jsonify(error="No se ha podido restablecer la contraseña."), 403
        except DatosCuentaInvalidos as error:
            return jsonify(error=str(error)), 400
        except (sqlite3.Error, OSError, HashingError):
            return jsonify(error="No se puede confirmar la operación. Consulta la cuenta antes de reintentar."), 503
        return jsonify(mensaje="Contraseña restablecida. El conductor debe cambiarla antes de acceder.")

    return api