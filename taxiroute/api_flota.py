"""API experimental de flota; requiere servicios autenticados inyectados.

No inicia servidor ni configura cuentas. La aplicación integradora debe exigir
HTTPS, restringir hosts y tamaño de petición, configurar autenticación y asegurar
que cada historial inyectado pertenece a la identidad validada.
"""
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida, RevisionDesactualizada
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE


def crear_api_flota(sesiones, flota, historial_por_usuario):
    """Las identidades y roles se obtienen exclusivamente de la sesión."""
    api = Blueprint("api_flota", __name__, url_prefix="/api")

    @api.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    def comprobar_transporte_y_origen(cambio=False):
        if not request.is_secure:
            abort(400)
        if request.args:
            abort(400)
        if cambio:
            if request.headers.get("Origin") != request.host_url.rstrip("/"):
                abort(403)
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            if not request.is_json:
                abort(415)

    def autorizada(operacion, *, interaccion=False):
        try:
            return sesiones.ejecutar_con_sesion_vigente(
                request.cookies.get(NOMBRE_COOKIE), roles={"conductor", "administrador"},
                operacion=operacion, interaccion=interaccion)
        except SesionNoAutorizada:
            abort(401)
        except RevisionDesactualizada:
            return jsonify(error="El estado ha cambiado. Actualiza la pantalla."), 409
        except FlotaBloqueada:
            return jsonify(error="La carrera necesita revisión antes de continuar."), 409
        except OperacionNoPermitida as error:
            return jsonify(error=str(error)), 409
        except (sqlite3.Error, OSError):
            return jsonify(error="No se puede confirmar el resultado. Revisa el estado antes de reintentar."), 503

    def empaquetar(resultado):
        if resultado is None:
            return {"activa": False, "estado": "sin carrera activa", "importe": None,
                    "pendiente": False, "revision": 0}
        return {"activa": resultado.activa, "estado": resultado.estado,
                "importe": str(resultado.importe) if resultado.importe is not None else None,
                "pendiente": resultado.pendiente, "revision": resultado.revision}

    def leer_datos(claves):
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or set(datos) != claves:
            abort(400)
        if type(datos.get("revision")) is not int or datos["revision"] < 0:
            abort(400)
        return datos

    @api.get("/carrera")
    def consultar():
        comprobar_transporte_y_origen()
        resultado = autorizada(lambda identidad: empaquetar(flota.consultar(identidad.usuario_id)))
        return resultado if isinstance(resultado, tuple) else jsonify(resultado)

    @api.get("/carrera/tarifas")
    def tarifas_carrera():
        comprobar_transporte_y_origen()
        def obtener(identidad):
            captura = flota.capturar(identidad.usuario_id)
            if captura is None:
                return {"tarifas": None, "revision": None}
            carrera = captura.carrera
            return {"tarifas": {"parado": format(carrera.tarifa_parado, "f"),
                                "en movimiento": format(carrera.tarifa_movimiento, "f")},
                    "revision": captura.revision}
        resultado = autorizada(obtener)
        return resultado if isinstance(resultado, tuple) else jsonify(resultado)

    def actuar(comando, revision):
        resultado = autorizada(lambda identidad: flota.ejecutar_controlado(
            identidad.usuario_id, comando, revision), interaccion=True)
        if isinstance(resultado, tuple):
            return resultado
        datos = empaquetar(resultado)
        if comando == "iniciar" and not resultado.activa:
            return jsonify(**datos, error=resultado.mensaje), 422
        if resultado.pendiente and comando in {"finalizar", "reintentar"}:
            aviso = ("No se ha podido guardar la carrera. "
                     + (resultado.motivo_guardado or "Comprueba el almacenamiento.")
                     + " Usa «Reintentar guardado»; si cierras la aplicación podrías perderla.")
            if comando == "reintentar":
                return jsonify(**datos, error=aviso), 503
            return jsonify(**datos, mensaje=aviso)
        return jsonify(**datos, mensaje=resultado.mensaje)

    @api.post("/carrera/iniciar")
    def iniciar():
        comprobar_transporte_y_origen(cambio=True)
        datos = leer_datos({"revision"})
        return actuar("iniciar", datos["revision"])

    @api.post("/carrera/estado")
    def cambiar_estado():
        comprobar_transporte_y_origen(cambio=True)
        datos = leer_datos({"revision", "estado"})
        estados = {"parado": "parar", "en movimiento": "mover"}
        if not isinstance(datos["estado"], str) or datos["estado"] not in estados:
            abort(400)
        return actuar(estados[datos["estado"]], datos["revision"])

    @api.post("/carrera/finalizar")
    def finalizar():
        comprobar_transporte_y_origen(cambio=True)
        datos = leer_datos({"revision"})
        return actuar("finalizar", datos["revision"])

    @api.post("/carrera/reintentar")
    def reintentar():
        comprobar_transporte_y_origen(cambio=True)
        datos = leer_datos({"revision"})
        return actuar("reintentar", datos["revision"])
    @api.get("/historial")
    def historial():
        comprobar_transporte_y_origen()
        def obtener(identidad):
            try:
                return historial_por_usuario(identidad.usuario_id).consultar()
            except (OSError, ValueError, TimeoutError):
                return None
        resultado = autorizada(obtener)
        if isinstance(resultado, tuple):
            return resultado
        if resultado is None:
            return jsonify(error="No se puede consultar el historial."), 503
        return jsonify(carreras=resultado)

    return api