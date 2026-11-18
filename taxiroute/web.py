"""Interfaz web local conectada a las operaciones de TaxiRoute."""
import argparse
from collections import deque
import json
from pathlib import Path
from secrets import token_urlsafe, compare_digest
from tempfile import TemporaryDirectory
from threading import RLock, Timer

from flask import Flask, jsonify, render_template, request, abort, g
from werkzeug.serving import make_server

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas, guardar_tarifas, ConfiguracionError
from taxiroute.historial import HistorialCSV
from taxiroute.registro import RegistroActividad
from taxiroute.servicio import ServicioCarreras


def crear_app(servicio=None, *, carpeta_datos=None, ruta_tarifas=None, modo_prueba=False,
              sesiones_prueba=None, pin_prueba=None, flota_prueba=None):
    """Permite inyectar el servicio o aislar datos para pruebas."""
    if sesiones_prueba is not None and (not modo_prueba or servicio is None):
        raise ValueError("La integración parcial solo admite pruebas con servicio inyectado.")
    if pin_prueba is not None and sesiones_prueba is None:
        raise ValueError("El PIN de prueba necesita el servicio de sesiones de prueba.")
    if flota_prueba is not None and sesiones_prueba is None:
        raise ValueError("La flota de prueba necesita sesiones de prueba.")
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=["127.0.0.1", "localhost"], MAX_CONTENT_LENGTH=4096)
    ruta_tarifas = Path(ruta_tarifas) if ruta_tarifas else Path(__file__).resolve().parent.parent / "configuracion" / "tarifas.json"
    if servicio is None:
        carpeta = Path(carpeta_datos) if carpeta_datos else Path.home() / ".taxiroute"
        servicio = ServicioCarreras(
            crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(ruta_tarifas)),
            historial=HistorialCSV(carpeta / "historial.csv"),
            registro=RegistroActividad(carpeta / "actividad.jsonl"),
        )
    app.extensions["servicio_carreras"] = servicio
    token = token_urlsafe(32)
    bloqueo = RLock()
    revision = 0
    cerrada = False
    ultimo_mensaje = "Aplicación preparada."
    servicio._registrar("aplicacion_iniciada", interfaz="web")

    def precios(tarifas):
        return {estado.value: str(valor) for estado, valor in tarifas.items()}

    def estado():
        activa = servicio.carrera is not None
        resumen = servicio.ultimo_resumen
        error_tarifas = None
        try:
            siguientes = precios(cargar_tarifas(ruta_tarifas))
        except ConfiguracionError as error:
            siguientes = None
            error_tarifas = str(error)
        aplicadas = precios(servicio.carrera.tarifas) if activa else None
        importe = servicio.carrera.importe_actual() if activa else resumen.importe if resumen else "0.00"
        return {
            "activa": activa, "pendiente": servicio.pendiente is not None,
            "estado": servicio.carrera.estado.value if activa else "sin carrera activa",
            "importe": str(importe), "finalizada": resumen is not None and not activa,
            "revision": revision, "cerrada": cerrada,
            "tarifas": aplicadas or siguientes, "tarifas_siguientes": siguientes,
            "error_tarifas": error_tarifas, "aviso_registro": servicio.aviso_registro,
            "mensaje": ultimo_mensaje, "modo_prueba": modo_prueba,
        }

    @app.before_request
    def proteger_operaciones():
        if sesiones_prueba is not None:
            # Integración parcial: bloquear todo salvo los endpoints probados.
            if not request.is_secure:
                abort(400)
            if request.endpoint in {"acceso_conductor.iniciar", "acceso_conductor.cerrar",
                                    "acceso_administrador.solicitar",
                                    "acceso_administrador.verificar",
                                    "acceso_administrador.cambiar"}:
                return None  # El blueprint valida origen, HTTPS y formato.
            if flota_prueba is not None and request.endpoint == "flota_accion":
                return None  # La ruta valida sesión, origen y contenido.
            if request.endpoint != "consultar":
                abort(404)
        if request.method == "POST":
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            origen = request.headers.get("Origin")
            if origen and origen != request.host_url.rstrip("/"):
                abort(403)
            recibido = request.headers.get("X-TaxiRoute-Token", "")
            if not compare_digest(recibido.encode("utf-8"), token.encode("ascii")):
                abort(403)

    @app.after_request
    def cabeceras(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.get("/")
    def inicio():
        return render_template("prototipo.html", token=token, modo_prueba=modo_prueba)

    @app.get("/prototipo/estado")
    def consultar():
        if flota_prueba is not None:
            # El identificador procede de la sesión validada, no de la URL.
            if request.args:
                abort(400)
            resultado = flota_prueba.consultar(g.identidad_taxiroute.usuario_id)
            return jsonify(
                activa=resultado.activa if resultado else False,
                estado=resultado.estado if resultado else "sin carrera activa",
                importe=str(resultado.importe) if resultado and resultado.importe is not None else None,
                pendiente=resultado.pendiente if resultado else False,
                revision=resultado.revision if resultado else 0,
            )
        with bloqueo:
            return jsonify(estado())

    @app.get("/prototipo/historial")
    def historial():
        filtro = request.args.get("filtro", "hoy")
        if filtro not in {"hoy", "todo"}:
            return jsonify(error="Filtro no válido."), 400
        with bloqueo:
            filas, error = servicio.consultar_historial(filtro == "hoy")
            if error:
                return jsonify(error=error), 409
            return jsonify(carreras=filas)

    @app.get("/prototipo/registros")
    def registros():
        if servicio.registro is None:
            return jsonify(eventos=[])
        ruta = servicio.registro.ruta
        try:
            with bloqueo_historial(ruta):
                if not ruta.exists():
                    return jsonify(eventos=[])
                with ruta.open(encoding="utf-8") as archivo:
                    ultimas = deque(archivo, maxlen=50)
            return jsonify(eventos=[json.loads(linea) for linea in ultimas])
        except (OSError, ValueError):
            return jsonify(error="No se puede leer el registro. Revisa el archivo o sus permisos."), 409

    @app.post("/prototipo/accion")
    def actuar():
        nonlocal revision, cerrada, ultimo_mensaje
        datos = request.get_json(silent=True)
        permitidas = {"iniciar", "mover", "parar", "finalizar", "reintentar", "exportar", "salir", "tarifas"}
        if not isinstance(datos, dict) or not isinstance(datos.get("accion"), str) or datos["accion"] not in permitidas:
            return jsonify(error="Acción no válida."), 400
        with bloqueo:
            if cerrada:
                return jsonify(error="La aplicación está cerrada. Inicia de nuevo el servicio."), 409
            if type(datos.get("revision")) is not int or datos["revision"] != revision:
                return jsonify(error="El estado ha cambiado. Revisa la pantalla e inténtalo de nuevo."), 409
            accion = datos["accion"]
            if accion == "iniciar" and (servicio.carrera or servicio.pendiente):
                return jsonify(error="Finaliza la carrera y resuelve el guardado pendiente antes de iniciar otra."), 409
            if accion in {"mover", "parar", "finalizar"} and servicio.carrera is None:
                return jsonify(error="No hay ninguna carrera activa."), 409
            if accion == "salir" and (servicio.carrera or servicio.pendiente):
                return jsonify(error="No se puede cerrar con una carrera activa o pendiente de guardar."), 409
            if accion in {"exportar", "reintentar"} and servicio.pendiente is None:
                return jsonify(error="No hay ninguna carrera pendiente de guardar."), 409
            if accion == "tarifas":
                try:
                    guardar_tarifas(ruta_tarifas, datos.get("tarifas"))
                except (OSError, ConfiguracionError) as error:
                    servicio._registrar_error("error_configuracion", error)
                    return jsonify(error=f"No se han guardado las tarifas: {error}"), 400
                servicio._registrar("tarifas_actualizadas")
                ultimo_mensaje = "Tarifas guardadas. Se aplicarán a la siguiente carrera; la activa conserva las suyas."
                revision += 1
                return jsonify(estado())
            comando = accion
            if accion == "exportar":
                ruta = datos.get("ruta")
                if not isinstance(ruta, str) or not ruta.strip() or any(c in ruta for c in "\r\n\x00"):
                    return jsonify(error="Indica una ruta válida para un archivo CSV nuevo."), 400
                comando = "exportar " + ruta
            ultimo_mensaje, salir = servicio.ejecutar(comando)
            revision += 1
            cerrada = salir
            respuesta = jsonify(estado())
            if salir and app.config.get("DETENER_SERVIDOR"):
                Timer(0.5, app.config["DETENER_SERVIDOR"]).start()
            return respuesta

    if sesiones_prueba is not None:
        from taxiroute.web_acceso import requerir_sesion
        from taxiroute.web_login import crear_acceso_conductor

        app.register_blueprint(crear_acceso_conductor(sesiones_prueba))
        if pin_prueba is not None:
            from taxiroute.web_admin_login import crear_acceso_administrador

            app.register_blueprint(crear_acceso_administrador(pin_prueba))
        if flota_prueba is not None:
            from taxiroute.flota import RevisionDesactualizada
            from taxiroute.sesiones import SesionNoAutorizada
            from taxiroute.web_acceso import NOMBRE_COOKIE

            @app.post("/prueba/flota/accion")
            @requerir_sesion(sesiones_prueba, roles={"conductor", "administrador"})
            def flota_accion():
                if request.headers.get("Origin") != request.host_url.rstrip("/"):
                    abort(403)
                if request.headers.get("Sec-Fetch-Site") == "cross-site":
                    abort(403)
                if request.args:
                    abort(400)
                if not request.is_json:
                    abort(415)
                datos = request.get_json(silent=True)
                if (not isinstance(datos, dict) or set(datos) != {"accion", "revision"}
                        or type(datos["revision"]) is not int or datos["revision"] < 0
                        or not isinstance(datos["accion"], str)
                        or datos["accion"] not in {"iniciar", "mover", "parar", "finalizar"}):
                    abort(400)
                try:
                    resultado = sesiones_prueba.ejecutar_con_sesion_vigente(
                        request.cookies.get(NOMBRE_COOKIE), roles={"conductor", "administrador"},
                        operacion=lambda identidad: flota_prueba.ejecutar_si_revision(
                            identidad.usuario_id, datos["accion"], datos["revision"]))
                except SesionNoAutorizada:
                    abort(401)
                except RevisionDesactualizada:
                    return jsonify(error="El estado ha cambiado. Actualiza la pantalla."), 409
                return jsonify(mensaje=resultado.mensaje, activa=resultado.activa,
                               estado=resultado.estado, pendiente=resultado.pendiente, revision=resultado.revision,
                               importe=str(resultado.importe) if resultado.importe is not None else None)
        app.view_functions["consultar"] = requerir_sesion(
            sesiones_prueba, roles={"conductor", "administrador"}
        )(app.view_functions["consultar"])

    return app


def main():
    parser = argparse.ArgumentParser(description="TaxiRoute web local")
    parser.add_argument("--demo", action="store_true", help="Usar archivos temporales, sin tocar el historial ni las tarifas de uso")
    args = parser.parse_args()
    temporal = TemporaryDirectory(prefix="taxiroute-demo-") if args.demo else None
    ruta_demo = None
    if temporal:
        ruta_demo = Path(temporal.name) / "tarifas.json"
        ruta_demo.write_text('{"parado":"0.02","en_movimiento":"0.05"}', encoding="utf-8")
    app = crear_app(carpeta_datos=temporal.name if temporal else None,
                    ruta_tarifas=ruta_demo, modo_prueba=args.demo)
    servidor = make_server("127.0.0.1", 8765, app, threaded=True)
    app.config["DETENER_SERVIDOR"] = servidor.shutdown
    print("TaxiRoute local: http://127.0.0.1:8765/", flush=True)
    print("Modo demostración: datos y tarifas temporales." if args.demo else "Historial de uso: ~/.taxiroute/historial.csv", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        servicio = app.extensions["servicio_carreras"]
        servicio._registrar("entrada_cerrada", interfaz="web",
                           carrera_activa=servicio.carrera is not None,
                           carrera_pendiente=servicio.pendiente is not None)
        print("Servicio detenido. Una carrera activa o pendiente no se recupera automáticamente.", flush=True)
    finally:
        servidor.server_close()
        if temporal:
            temporal.cleanup()


if __name__ == "__main__":
    main()
