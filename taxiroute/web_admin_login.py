"""Acceso administrativo experimental; no registrado en la web principal."""
from flask import Blueprint, abort, jsonify, request

from taxiroute.acceso import CambioContrasenaDenegado
from taxiroute.cambio_administrador import cambiar_contrasena_administrador
from taxiroute.cuentas import DatosCuentaInvalidos
from taxiroute.web_acceso import NOMBRE_COOKIE

COOKIE_PIN = "taxiroute_pin_pendiente"
COOKIE_CAMBIO = "taxiroute_cambio_pendiente"
RUTA_ADMIN = "/acceso/administrador"


def crear_acceso_administrador(servicio_pin):
    """El servidor debe restringir hosts, tamaño y velocidad de las peticiones."""
    acceso = Blueprint("acceso_administrador", __name__)

    @acceso.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    def datos_seguros(claves):
        if not request.is_secure:
            abort(400)
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        if not request.is_json:
            abort(415)
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or set(datos) != claves:
            abort(400)
        if any(not isinstance(valor, str) for valor in datos.values()):
            abort(400)
        return datos

    def cookie_temporal(respuesta, nombre, valor):
        respuesta.set_cookie(nombre, valor, max_age=300, secure=True, httponly=True,
                            samesite="Strict", path=RUTA_ADMIN)

    def borrar_temporal(respuesta, nombre):
        respuesta.delete_cookie(nombre, path=RUTA_ADMIN, secure=True, httponly=True,
                                samesite="Strict")

    @acceso.post(RUTA_ADMIN)
    def solicitar():
        datos = datos_seguros({"usuario", "contrasena"})
        desafio = servicio_pin.solicitar(datos["usuario"], datos["contrasena"])
        if desafio is None:
            return jsonify(error="No se ha podido continuar el acceso. Comprueba las credenciales y la disponibilidad del correo."), 401
        respuesta = jsonify(paso="pin_pendiente")
        borrar_temporal(respuesta, COOKIE_CAMBIO)
        cookie_temporal(respuesta, COOKIE_PIN, desafio)
        return respuesta

    @acceso.post(RUTA_ADMIN + "/pin")
    def verificar():
        datos = datos_seguros({"codigo"})
        desafio = request.cookies.get(COOKIE_PIN)
        resultado = servicio_pin.verificar(desafio, datos["codigo"])
        if resultado is None:
            return jsonify(error="No se ha podido verificar el código."), 401
        if resultado.cambio_obligatorio:
            respuesta = jsonify(paso="cambio_pendiente")
            cookie_temporal(respuesta, COOKIE_CAMBIO, resultado.autorizacion_cambio)
        else:
            respuesta = jsonify(paso="acceso_completado")
            respuesta.set_cookie(NOMBRE_COOKIE, resultado.sesion.token, path="/",
                                 secure=True, httponly=True, samesite="Strict")
        borrar_temporal(respuesta, COOKIE_PIN)
        return respuesta

    @acceso.post(RUTA_ADMIN + "/cambio")
    def cambiar():
        datos = datos_seguros({"contrasena_nueva"})
        autorizacion = request.cookies.get(COOKIE_CAMBIO)
        try:
            cambiar_contrasena_administrador(
                servicio_pin.ruta, autorizacion, datos["contrasena_nueva"],
                reloj=servicio_pin._reloj,
            )
        except (CambioContrasenaDenegado, DatosCuentaInvalidos):
            return jsonify(error="No se ha podido cambiar la contraseña."), 400
        respuesta = jsonify(paso="volver_a_iniciar")
        borrar_temporal(respuesta, COOKIE_CAMBIO)
        return respuesta

    return acceso
