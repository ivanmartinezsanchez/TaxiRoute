"""Rutas experimentales para revisar acciones interrumpidas."""
import sqlite3
from uuid import UUID

from flask import Blueprint, abort, current_app, jsonify, request

from taxiroute.finalizacion_pendiente import _importe
from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida
from taxiroute.lectura_pendientes import (PendienteInvalida, huella_intencion,
                                         leer_estado_pendiente_en_transaccion)
from taxiroute.resolver_intencion import resolver_intencion_revisada
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


def crear_web_resolucion_intencion(sesiones, flota):
    """La aplicación debe mantener bloqueo exclusivo de instancia y HTTPS."""
    if not flota._modo_durable_experimental:
        raise ValueError("Se necesita una flota durable experimental.")
    api = Blueprint("web_resolucion_intencion", __name__, url_prefix="/api/recuperacion")

    @api.before_request
    def proteger():
        if current_app.config.get("RECUPERACION_HABILITADA") is not True:
            abort(404)
        if not request.is_secure or request.args:
            abort(400)
        if request.method == "POST":
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

    def usuario_valido(usuario_id):
        try:
            if str(UUID(usuario_id)) != usuario_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            abort(400)

    def validar_cuerpo():
        datos = request.get_json(silent=True)
        if (not isinstance(datos, dict) or set(datos) != {"huella", "confirmar"}
                or datos["confirmar"] is not True or not isinstance(datos["huella"], str)
                or len(datos["huella"]) != 64 or any(c not in "0123456789abcdef" for c in datos["huella"])):
            abort(400)
        return datos

    def sin_autorizacion(token):
        try:
            vigente = sesiones.consultar(token)
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede comprobar la sesión."), 503
        return jsonify(error="No se puede realizar esta revisión."), 403 if vigente is not None else 401

    @api.get("/administracion/<usuario_id>/intencion")
    @requerir_sesion(sesiones, roles={"administrador"})
    def consultar(usuario_id):
        usuario_valido(usuario_id)
        token = request.cookies.get(NOMBRE_COOKIE)
        def leer(identidad, conexion):
            usuario = conexion.execute("SELECT 1 FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if usuario is None:
                return None
            punto, intencion = leer_estado_pendiente_en_transaccion(conexion, usuario_id)
            if intencion is None:
                return {"intencion": None, "pendiente": None}
            pendiente = None if punto is None else {
                "inicio": punto.punto.inicio.isoformat(),
                "ultimo_control": punto.punto.ultimo_control.isoformat(),
                "estado_vehiculo": punto.punto.estado_vehiculo,
                "estado": punto.estado,
                "importe_recuperado": str(_importe(punto.punto)),
            }
            return {"intencion": {"accion": intencion.accion, "huella": huella_intencion(intencion, punto)},
                    "pendiente": pendiente}
        try:
            resultado = sesiones.ejecutar_con_sesion_y_conexion(token, roles={"administrador"}, operacion=leer)
            if resultado is None:
                return jsonify(error="No se encuentra la cuenta."), 404
            return jsonify(resultado)
        except SesionNoAutorizada:
            return sin_autorizacion(token)
        except (PendienteInvalida, ValueError):
            return jsonify(error="La información necesita revisión."), 409
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede consultar la revisión."), 503

    def resolver(token, usuario_id, datos):
        try:
            resultado = resolver_intencion_revisada(sesiones, token, flota, usuario_id,
                huella=datos["huella"], confirmar=datos["confirmar"])
        except SesionNoAutorizada:
            return sin_autorizacion(token)
        except (FlotaBloqueada, OperacionNoPermitida, PendienteInvalida, ValueError):
            return jsonify(error="No se puede confirmar la revisión. Actualiza los datos."), 409
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede confirmar la revisión. Vuelve a comprobar el estado."), 503
        mensaje = ("Acción revisada. Ya se puede iniciar otra carrera." if resultado == "inicio_revisado"
                   else "Acción revisada. La carrera sigue pendiente de cerrar en el último punto fiable.")
        return jsonify(resultado=resultado, mensaje=mensaje)

    @api.post("/administracion/<usuario_id>/intencion/resolver")
    @requerir_sesion(sesiones, roles={"administrador"})
    def resolver_administrador(usuario_id):
        usuario_valido(usuario_id)
        return resolver(request.cookies.get(NOMBRE_COOKIE), usuario_id, validar_cuerpo())

    @api.post("/intencion/confirmar")
    @requerir_sesion(sesiones, roles={"conductor", "administrador"})
    def resolver_propia():
        token = request.cookies.get(NOMBRE_COOKIE)
        identidad = sesiones.consultar(token)
        if identidad is None:
            return jsonify(error="No se puede realizar esta revisión."), 401
        return resolver(token, identidad.usuario_id, validar_cuerpo())

    return api
