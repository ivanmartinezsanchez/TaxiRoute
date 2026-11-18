"""Administración web experimental. No registra rutas por sí misma.

La aplicación integradora debe restringir hosts, tamaño de solicitudes y TLS.
No incluye alta ni restablecimiento de contraseñas.
"""
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.administracion import (AdministracionDenegada, CarreraPendienteAdministracion,
                                      EstadoFlotaNoDisponible, administrar_conductor)
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


_ACCIONES = frozenset({"desactivar", "reactivar", "desbloquear", "revocar"})


def crear_administracion_web(sesiones, flota=None):
    """Construye rutas de lista y acciones para sesiones administrativas."""
    api = Blueprint("administracion_web", __name__, url_prefix="/api/administracion")

    @api.before_request
    def comprobar_solicitud():
        if not request.is_secure:
            abort(400)
        if request.args:
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

    @api.get("/conductores")
    @requerir_sesion(sesiones, roles={"administrador"})
    def listar():
        def consultar(identidad, conexion):
            filas = conexion.execute("""SELECT id, nombre_usuario, nombre_visible, habilitado,
                       bloqueado_en, intentos_fallidos FROM usuarios
                       WHERE rol='conductor' ORDER BY nombre_usuario_normalizado""").fetchall()
            return [{"id": fila["id"], "usuario": fila["nombre_usuario"],
                     "nombre_visible": fila["nombre_visible"],
                     "habilitado": bool(fila["habilitado"]),
                     "bloqueado": fila["bloqueado_en"] is not None or fila["intentos_fallidos"] >= 15}
                    for fila in filas]
        try:
            resultado = sesiones.ejecutar_con_sesion_y_conexion(
                request.cookies.get(NOMBRE_COOKIE), roles={"administrador"}, operacion=consultar)
        except SesionNoAutorizada:
            abort(401)
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede consultar la administración."), 503
        return jsonify(conductores=resultado)

    @api.post("/conductores/<usuario_id>/accion")
    @requerir_sesion(sesiones, roles={"administrador"})
    def actuar(usuario_id):
        datos = request.get_json(silent=True)
        if (not isinstance(datos, dict) or set(datos) != {"accion"}
                or not isinstance(datos["accion"], str) or datos["accion"] not in _ACCIONES):
            abort(400)
        try:
            administrar_conductor(sesiones.ruta, request.cookies.get(NOMBRE_COOKIE),
                                  usuario_id, datos["accion"], reloj=sesiones._reloj, flota=flota)
        except CarreraPendienteAdministracion as error:
            return jsonify(error=str(error)), 409
        except EstadoFlotaNoDisponible:
            return jsonify(error="No se puede comprobar el estado de la carrera; no se ha desactivado la cuenta."), 503
        except AdministracionDenegada:
            return jsonify(error="No se ha podido realizar la operación."), 403
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede confirmar la operación. Actualiza el estado."), 503
        return jsonify(mensaje="Operación realizada.")

    return api