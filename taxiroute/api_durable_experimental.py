"""API aislada para probar la coordinación durable; no es un despliegue real.

Se usa en demostraciones ficticias. La recuperación interactiva se habilita
solo en la demo persistente con bloqueo exclusivo. No configura por sí sola
los puntos periódicos ni constituye un servidor de producción.
"""
import errno
import sqlite3

from flask import Blueprint, abort, current_app, jsonify, request

from taxiroute.conciliacion_sqlite import conciliar_pendiente_sqlite
from taxiroute.conciliacion_csv import conciliar_pendiente_csv_autorizado
from taxiroute.consulta_historial_csv import consultar_historial_csv_verificado
from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida, RevisionDesactualizada
from taxiroute.intenciones_operacion import IntencionNoPermitida, registrar_intencion
from taxiroute.liberacion_cierre import liberar_cierre_conciliado
from taxiroute.liberacion_cierre_csv import liberar_cierre_csv
from taxiroute.liberacion_recuperada_csv import liberar_recuperada_csv
from taxiroute.recibos_operacion import consultar_recibo
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE


def crear_api_durable_experimental(sesiones, flota, ruta_historial, *, prefijo="/api/durable",
                                   formato_historial="sqlite"):
    """Integra el protocolo experimental con una ruta y formato del servidor.

    En CSV el integrador debe verificar previamente el vínculo de instalación,
    mantener el bloqueo de proceso y gestionar el control periódico. Las
    operaciones de CSV y SQLite no forman una única transacción atómica.
    """
    if not flota._modo_durable_experimental:
        raise ValueError("La API requiere el modo durable experimental.")
    if formato_historial not in {"sqlite", "csv"}:
        raise ValueError("El formato de historial no es válido.")
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
        if formato_historial == "csv":
            carreras = consultar_historial_csv_verificado(sesiones, token, ruta_historial)
        else:
            carreras = HistorialSQLite(ruta_historial, usuario_id=identidad.usuario_id, exigir_existente=True).consultar()
        # La lectura del historial no debe mantener abierta la transacción de
        # cuentas: la conciliación adquiere historial antes que cuentas.
        vigente = sesiones.consultar(token)
        if vigente is None or vigente.usuario_id != identidad.usuario_id or vigente.rol != identidad.rol:
            raise SesionNoAutorizada()
        return jsonify(carreras=carreras)

    def conciliar(token, identidad, carrera_id):
        if formato_historial == "csv":
            return conciliar_pendiente_csv_autorizado(sesiones, token, ruta_historial, carrera_id)
        return conciliar_pendiente_sqlite(sesiones.ruta, ruta_historial, identidad.usuario_id, carrera_id)

    def liberar_cierre(token):
        if formato_historial == "csv":
            return liberar_cierre_csv(flota, sesiones, token, ruta_historial)
        return liberar_cierre_conciliado(flota, sesiones, token, ruta_historial)

    def liberar_revisada(token, *, revision, instante, huella):
        if formato_historial == "csv":
            return liberar_recuperada_csv(sesiones, token, flota, ruta_historial,
                revision_esperada=revision, ultimo_control_esperado=instante, huella_esperada=huella)
        from taxiroute.recuperacion_ultima_captura import liberar_recuperada
        return liberar_recuperada(sesiones, token, flota, ruta_historial,
            revision_esperada=revision, ultimo_control_esperado=instante, huella_esperada=huella)

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
        conciliar(token, identidad, resumen.punto.carrera_id)
        liberar_cierre(token)
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), mensaje="Carrera guardada en el historial.")

    def exigir_demo_persistente():
        if current_app.config.get("DEMO_PERSISTENTE") is not True:
            abort(404)

    def consultar_guardada_bloqueada(token):
        from taxiroute.conciliacion_csv import _leer
        from taxiroute.puntos_control import _desde_fila
        def leer(identidad, conexion):
            with flota._bloqueo:
                usuario = identidad.usuario_id
                servicio = flota._servicios.get(usuario)
                if (usuario not in flota._bloqueados or usuario in flota._cierres_preparados
                        or (servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None))
                        or conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario,)).fetchone()
                        or conexion.execute("SELECT 1 FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro!='guardada'", (usuario,)).fetchone()):
                    raise OperacionNoPermitida()
                revision = flota._revisiones.get(usuario, 0)
                filas = conexion.execute("SELECT * FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro='guardada' AND revision=?", (usuario, revision)).fetchall()
                if len(filas) != 1:
                    raise OperacionNoPermitida()
                _leer(conexion, usuario, filas[0]["id"])
                return _desde_fila(filas[0]), "guardada"
        return sesiones.ejecutar_con_sesion_y_conexion(token,
            roles={"conductor", "administrador"}, operacion=leer)

    @api.get("/carrera/revision-recuperacion")
    def consultar_revision_recuperacion():
        exigir_demo_persistente()
        from taxiroute.finalizacion_pendiente import _importe
        from taxiroute.recuperacion_ultima_captura import revisar_ultima_captura, huella_revision
        token, _ = token_y_identidad()
        try:
            punto, estado = revisar_ultima_captura(sesiones, token, flota)
        except OperacionNoPermitida:
            punto, estado = consultar_guardada_bloqueada(token)
        revision = punto.revision - 1 if estado in {"guardado_pendiente", "guardada"} else punto.revision
        return jsonify(pendiente={"estado": estado, "inicio": punto.inicio.isoformat(),
            "ultimo_control": punto.ultimo_control.isoformat(), "estado_vehiculo": punto.estado_vehiculo,
            "revision_recuperacion": revision, "importe_recuperado": str(_importe(punto)),
            "huella": huella_revision(punto, revision),
            "revision_disponible": True})

    @api.post("/carrera/confirmar-recuperacion")
    def confirmar_recuperacion():
        exigir_demo_persistente()
        from datetime import datetime
        from taxiroute.recuperacion_ultima_captura import cerrar_en_ultima_captura
        datos = datos_con_revision({"revision", "ultimo_control", "confirmar", "huella"})
        if (datos["confirmar"] is not True or not isinstance(datos["ultimo_control"], str)
                or not isinstance(datos["huella"], str) or len(datos["huella"]) != 64
                or any(c not in "0123456789abcdef" for c in datos["huella"])):
            abort(400)
        try:
            instante = datetime.fromisoformat(datos["ultimo_control"])
            if instante.tzinfo is None or instante.utcoffset() is None:
                abort(400)
        except ValueError:
            abort(400)
        token, identidad = token_y_identidad()
        try:
            resumen = cerrar_en_ultima_captura(sesiones, token, flota,
                revision_esperada=datos["revision"], ultimo_control_esperado=instante, huella_esperada=datos["huella"])
        except OperacionNoPermitida:
            # Puede haber quedado guardada antes de que se perdiera la respuesta.
            liberar_revisada(token, revision=datos["revision"], instante=instante, huella=datos["huella"])
        else:
            conciliar(token, identidad, resumen.punto.carrera_id)
            liberar_revisada(token, revision=datos["revision"], instante=instante, huella=datos["huella"])
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), mensaje="Carrera recuperada y guardada hasta el último punto fiable.")

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
        conciliar(token, identidad, pendiente.punto.carrera_id)
        liberar_cierre(token)
        registrar_interaccion(token, identidad)
        return jsonify(**estado_actual(token), mensaje="Carrera guardada en el historial.")

    return api
