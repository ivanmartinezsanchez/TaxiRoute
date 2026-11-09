"""Consulta web aislada de la carrera pendiente de la cuenta autenticada."""
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.lectura_pendientes import PendienteInvalida, consultar_pendiente_propia
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE


def crear_web_pendientes(sesiones):
    """La aplicación integradora debe restringir los hosts y exigir HTTPS."""
    api = Blueprint("web_pendientes", __name__, url_prefix="/api/recuperacion")

    @api.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @api.get("/pendiente")
    def pendiente():
        if not request.is_secure or request.args:
            abort(400)
        try:
            resultado = consultar_pendiente_propia(sesiones, request.cookies.get(NOMBRE_COOKIE))
        except SesionNoAutorizada:
            abort(401)
        except (PendienteInvalida, sqlite3.Error, OSError):
            return jsonify(error="No se puede consultar la carrera pendiente."), 503
        if resultado is None:
            return jsonify(pendiente=None)
        punto = resultado.punto
        return jsonify(pendiente={
            "carrera_id": punto.carrera_id,
            "estado": resultado.estado,
            "inicio": punto.inicio.isoformat(),
            "ultimo_control": punto.ultimo_control.isoformat(),
            "estado_vehiculo": punto.estado_vehiculo,
            # Las cadenas evitan pérdida de precisión en números grandes de JavaScript.
            "parado_ns": str(punto.parado_ns),
            "movimiento_ns": str(punto.movimiento_ns),
            "tarifa_parado": punto.tarifa_parado,
            "tarifa_movimiento": punto.tarifa_movimiento,
            "revision": punto.revision,
            "fin": resultado.fin.isoformat() if resultado.fin is not None else None,
            "importe_final": str(resultado.importe_final) if resultado.importe_final is not None else None,
        })

    return api
