"""Inicio web experimental de conductor; no registrado en la aplicación principal."""
from flask import Blueprint, abort, jsonify, request

from taxiroute.web_acceso import NOMBRE_COOKIE


def crear_acceso_conductor(servicio_sesiones):
    """Crea rutas aisladas; quien las integre debe configurar hosts y HTTPS.

    Solo admite conductores sin cambio obligatorio. Administradores y cuentas
    temporales requieren otros flujos aún pendientes.
    """
    acceso = Blueprint("acceso_conductor", __name__)

    def comprobar_origen():
        """Requiere transporte seguro y mismo origen antes de modificar estado."""
        # La aplicación integradora debe restringir los Host aceptados.
        if not request.is_secure:
            abort(400)
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
    @acceso.post("/acceso/conductor")
    def iniciar():
        comprobar_origen()
        if not request.is_json:
            abort(415)
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or set(datos) != {"usuario", "contrasena"}:
            return jsonify(error="Datos de acceso no válidos."), 400
        if not isinstance(datos["usuario"], str) or not isinstance(datos["contrasena"], str):
            return jsonify(error="Datos de acceso no válidos."), 400
        sesion = servicio_sesiones.iniciar_conductor(datos["usuario"], datos["contrasena"])
        if sesion is None:
            return jsonify(error="No se ha podido iniciar sesión."), 401
        respuesta = jsonify(mensaje="Acceso de conductor completado.")
        respuesta.set_cookie(
            NOMBRE_COOKIE, sesion.token, secure=True, httponly=True,
            samesite="Strict", path="/",
        )
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @acceso.post("/acceso/cerrar")
    def cerrar():
        comprobar_origen()
        # Revocar primero: si falla el almacenamiento, no se anuncia un cierre.
        servicio_sesiones.cerrar(request.cookies.get(NOMBRE_COOKIE))
        respuesta = jsonify(mensaje="Sesión cerrada.")
        respuesta.delete_cookie(
            NOMBRE_COOKIE, path="/", secure=True, httponly=True, samesite="Strict",
        )
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    return acceso
