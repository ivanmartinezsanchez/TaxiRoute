"""Tarifas web experimentales; no registra rutas por sí mismo.

La aplicación integradora debe configurar hosts, TLS y tamaño de solicitudes.
El archivo de tarifas y la base SQLite no comparten una única transacción.
"""
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.configuracion import ConfiguracionError, cargar_tarifas, guardar_tarifas
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


def crear_tarifas_web(sesiones, ruta_tarifas):
    """Consulta para ambas cuentas y modificación solo administrativa."""
    api = Blueprint("tarifas_web", __name__, url_prefix="/api/tarifas")

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

    @api.get("")
    @requerir_sesion(sesiones, roles={"conductor", "administrador"})
    def consultar():
        def leer(identidad):
            return {estado.value: str(valor) for estado, valor in cargar_tarifas(ruta_tarifas).items()}
        try:
            tarifas = sesiones.ejecutar_con_sesion_vigente(
                request.cookies.get(NOMBRE_COOKIE), roles={"conductor", "administrador"}, operacion=leer)
        except SesionNoAutorizada:
            abort(401)
        except (ConfiguracionError, sqlite3.Error, OSError):
            return jsonify(error="No se pueden consultar las tarifas."), 503
        return jsonify(tarifas=tarifas)

    @api.post("")
    @requerir_sesion(sesiones, roles={"administrador"})
    def guardar():
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or set(datos) != {"parado", "en_movimiento"}:
            abort(400)
        def actualizar(identidad):
            guardar_tarifas(ruta_tarifas, datos)
        try:
            sesiones.ejecutar_con_sesion_vigente(
                request.cookies.get(NOMBRE_COOKIE), roles={"administrador"}, operacion=actualizar, interaccion=True)
        except SesionNoAutorizada:
            abort(401)
        except ConfiguracionError:
            return jsonify(error="Tarifas no válidas. Revisa los importes indicados."), 400
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede confirmar el guardado. Comprueba las tarifas."), 503
        return jsonify(mensaje="Tarifas guardadas. Se aplicarán a las carreras nuevas.")

    return api