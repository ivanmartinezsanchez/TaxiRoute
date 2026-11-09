"""API aislada para probar la coordinación durable; no es un despliegue real.

Solo se registra explícitamente en pruebas. El historial y las cuentas deben
ser archivos temporales confiables. No implementa puntos periódicos, recuperación
interactiva, exclusión entre procesos ni un servidor persistente de producción.
"""
import errno
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite
from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida, RevisionDesactualizada
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.liberacion_cierre import liberar_cierre_conciliado
from taxiroute.recibos_operacion import consultar_recibo
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE


def crear_api_durable_experimental(sesiones, flota, ruta_historial, *, prefijo="/api/durable"):
    """Integra únicamente el protocolo de un proceso y rutas preconfiguradas."""
    if not flota._modo_durable_experimental:
        raise ValueError("La API requiere el modo durable experimental.")
    api = Blueprint("api_durable_experimental", __name__, url_prefix=prefijo)

    @api.before_request
    def validar_solicitud():
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
    def no_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    def token_y_identidad():
        token = request.cookies.get(NOMBRE_COOKIE)
        identidad = sesiones.consultar(token)
        if identidad is None or identidad.rol not in {"conductor", "administrador"}:
            raise SesionNoAutorizada()
        return token, identidad

    @api.errorhandler(SesionNoAutorizada)
    def sesion_invalida(error):
        return jsonify(error="La sesión no está disponible."), 401

    @api.errorhandler(FlotaBloqueada)
    def bloqueada(error):
        return jsonify(error="La carrera necesita revisión antes de continuar."), 409

    @api.errorhandler(OperacionNoPermitida)
    @api.errorhandler(RevisionDesactualizada)
    @api.errorhandler(IntencionNoPermitida)
    def rechazada(error):
        return jsonify(error="La operación no coincide con el estado disponible. Revisa la carrera."), 409

    @api.errorhandler(OSError)
    @api.errorhandler(ValueError)
    @api.errorhandler(sqlite3.Error)
    def almacenamiento(error):
        motivo = "No se puede determinar la causa con seguridad."
        actual = error
        vistos = set()
        while actual is not None and id(actual) not in vistos:
            vistos.add(id(actual))
            codigo = getattr(actual, "sqlite_errorcode", None)
            if (isinstance(actual, OSError) and
                    (actual.errno == errno.ENOSPC or getattr(actual, "winerror", None) in (39, 112))) or codigo == sqlite3.SQLITE_FULL:
                motivo = "No queda espacio en la unidad."
                break
            if isinstance(actual, PermissionError) or codigo in {sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM}:
                motivo = "No hay permiso para guardar los datos."
                break
            if codigo in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                motivo = "El almacenamiento está ocupado por otra operación."
                break
            actual = actual.__cause__
        return jsonify(error=f"No se puede confirmar el resultado. {motivo} La carrera puede requerir revisión."), 503

    def registrar_interaccion(token, identidad):
        vigente = sesiones.consultar(token, interaccion=True)
        if vigente is None or vigente.usuario_id != identidad.usuario_id or vigente.rol != identidad.rol:
            raise SesionNoAutorizada()

    def datos_con_revision(claves):
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or set(datos) != claves:
            abort(400)
        if type(datos.get("revision")) is not int or not 0 <= datos["revision"] < 2**63 - 1:
            abort(400)
        return datos

    def estado_actual(token):
        def leer(autorizada):
            resultado = flota.consultar(autorizada.usuario_id)
            if resultado is None:
                with flota._bloqueo:
                    revision = flota._revisiones.get(autorizada.usuario_id, 0)
                return {"activa": False, "estado": "sin carrera activa", "importe": None,
                        "pendiente": False, "revision": revision}
            return {"activa": resultado.activa, "estado": resultado.estado,
                    "importe": str(resultado.importe) if resultado.importe is not None else None,
                    "pendiente": resultado.pendiente, "revision": resultado.revision}
        return sesiones.ejecutar_con_sesion_vigente(token,
            roles={"conductor", "administrador"}, operacion=leer)

    @api.get("/carrera")
    def consultar():
        token, _ = token_y_identidad()
        return jsonify(estado_actual(token))

    @api.get("/carrera/tarifas")
    def consultar_tarifas_carrera():
        token, _ = token_y_identidad()
        def leer(identidad):
            captura = flota.capturar(identidad.usuario_id)
            if captura is None:
                return {"tarifas": None, "revision": None}
            return {"tarifas": {"parado": format(captura.carrera.tarifa_parado, "f"),
                                "en movimiento": format(captura.carrera.tarifa_movimiento, "f")},
                    "revision": captura.revision}
        return jsonify(sesiones.ejecutar_con_sesion_vigente(token,
            roles={"conductor", "administrador"}, operacion=leer))

    @api.get("/historial")
    def consultar_historial():
        from taxiroute.historial_sqlite import HistorialSQLite
        token, identidad = token_y_identidad()
        carreras = HistorialSQLite(ruta_historial, usuario_id=identidad.usuario_id, exigir_existente=True).consultar()
        # La lectura del historial no debe mantener abierta la transacción de
        # cuentas: la conciliación adquiere historial antes que cuentas.
        vigente = sesiones.consultar(token)
        if vigente is None or vigente.usuario_id != identidad.usuario_id or vigente.rol != identidad.rol:
            raise SesionNoAutorizada()
        return jsonify(carreras=carreras)

    def aplicar(accion, datos):
        token, identidad = token_y_identidad()
        recibo = consultar_recibo(sesiones, token, datos["id_operacion"])
        if recibo is not None:
            if recibo.accion != accion or recibo.revision != datos["revision"]:
                raise IntencionNoPermitida()
            registrar_interaccion(token, identidad)
            return jsonify(**estado_actual(token), repetida=True,
                           mensaje="Operación ya registrada. Consulta el estado actual.")
        carrera_id = None
        if accion != "iniciar":
            captura = flota.capturar(identidad.usuario_id)
            if captura is None or captura.revision != datos["revision"]:
                raise RevisionDesactualizada()
            carrera_id = captura.carrera.identificador
        registrar_intencion(sesiones, token, id_intencion=datos["id_operacion"],
                            accion=accion, revision=datos["revision"], carrera_id=carrera_id)
        punto = flota.aplicar_intencion_experimental(sesiones, token, datos["id_operacion"])
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), repetida=False, mensaje="Operación registrada.")

    @api.post("/carrera/iniciar")
    def iniciar():
        return aplicar("iniciar", datos_con_revision({"revision", "id_operacion"}))

    @api.post("/carrera/estado")
    def estado():
        datos = datos_con_revision({"revision", "id_operacion", "estado"})
        opciones = {"parado": "parar", "en movimiento": "mover"}
        if not isinstance(datos["estado"], str) or datos["estado"] not in opciones:
            abort(400)
        return aplicar(opciones[datos["estado"]], datos)

    @api.post("/carrera/finalizar")
    def finalizar():
        datos = datos_con_revision({"revision"})
        token, identidad = token_y_identidad()
        resumen = flota.preparar_cierre_durable_experimental(sesiones, token, datos["revision"])
        conciliar_pendiente_sqlite(sesiones.ruta, ruta_historial,
                                    identidad.usuario_id, resumen.punto.carrera_id)
        final = liberar_cierre_conciliado(flota, sesiones, token, ruta_historial)
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), mensaje="Carrera guardada en el historial.")

    @api.get("/carrera/cierre-pendiente")
    def consultar_cierre_propio():
        from taxiroute.conciliacion_csv import _leer
        from taxiroute.finalizacion_pendiente import resumen_para_historial
        from taxiroute.historial import _fila
        token, _ = token_y_identidad()
        def leer(identidad, conexion):
            # Cuentas → flota, el mismo orden de las acciones de carrera.
            with flota._bloqueo:
                marcador = flota._cierres_preparados.get(identidad.usuario_id)
                if marcador is None or identidad.usuario_id not in flota._bloqueados:
                    return None
                punto = marcador.punto
                _, estado, resumen = _leer(conexion, identidad.usuario_id, punto.carrera_id)
                if _fila(resumen) != _fila(resumen_para_historial(marcador)):
                    raise OperacionNoPermitida()
                return {"estado": estado, "inicio": punto.inicio.isoformat(),
                        "ultimo_control": punto.ultimo_control.isoformat(),
                        "estado_vehiculo": punto.estado_vehiculo,
                        "revision_reintento": punto.revision - 1,
                        "reintento_disponible": True}
        resultado = sesiones.ejecutar_con_sesion_y_conexion(token,
            roles={"conductor", "administrador"}, operacion=leer)
        return jsonify(pendiente=resultado)

    @api.post("/carrera/reintentar")
    def reintentar_guardado():
        datos = datos_con_revision({"revision"})
        token, identidad = token_y_identidad()
        # El marcador procede exclusivamente del servidor y de la sesión; el
        # navegador no elige ni la cuenta, ni la carrera, ni la ruta de archivos.
        with flota._bloqueo:
            pendiente = flota._cierres_preparados.get(identidad.usuario_id)
            if (identidad.usuario_id not in flota._bloqueados or pendiente is None
                    or pendiente.punto.revision != datos["revision"] + 1):
                raise OperacionNoPermitida()
        conciliar_pendiente_sqlite(sesiones.ruta, ruta_historial,
                                    identidad.usuario_id, pendiente.punto.carrera_id)
        liberar_cierre_conciliado(flota, sesiones, token, ruta_historial)
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), mensaje="Carrera guardada en el historial.")

    return api
