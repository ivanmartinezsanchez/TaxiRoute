"""Fábrica experimental HTTPS para integrar acceso, API y pantallas.

No inicia servidor ni crea cuentas. La configuración real, recuperación de
carreras, limitación de accesos entre procesos y despliegue siguen pendientes.
El servidor que la ejecute debe proporcionar TLS directo y seguro.
"""
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from taxiroute.api_flota import crear_api_flota
from taxiroute.limitador_acceso import LimitadorAcceso
from taxiroute.web_acceso import requerir_sesion
from taxiroute.web_admin_login import crear_acceso_administrador
from taxiroute.web_login import crear_acceso_conductor
from taxiroute.web_administracion import crear_administracion_web
from taxiroute.web_tarifas import crear_tarifas_web
from taxiroute.web_cambio_conductor import crear_cambio_conductor
from taxiroute.web_alta_conductor import crear_alta_conductor_web
from taxiroute.web_pendientes import crear_web_pendientes


_RECURSOS = frozenset({"acceso.css", "acceso.js", "taximetro_seguro.css", "taximetro_seguro.js",
                        "administracion.css", "administracion.js", "pantalla_activa.js"})
_RUTAS_ACCESO = frozenset({"/acceso/conductor", "/acceso/conductor/cambio", "/acceso/administrador",
                            "/acceso/administrador/pin", "/acceso/administrador/cambio"})


def crear_app_integrada(sesiones, pin, flota, historial_por_usuario, *, ruta_tarifas=None,
                        ruta_historial_durable=None, ruta_historial_flota=None, limitador_acceso=None):
    """Servicios inyectados por el integrador; no usar como aplicación final."""
    if not callable(historial_por_usuario):
        raise ValueError("Se necesita un historial por usuario.")
    if Path(sesiones.ruta).resolve() != Path(pin.ruta).resolve():
        raise ValueError("El PIN y las sesiones deben usar la misma base de cuentas.")
    limitador = LimitadorAcceso() if limitador_acceso is None else limitador_acceso
    if not callable(getattr(limitador, "esperar_segundos", None)):
        raise ValueError("Se necesita un limitador de acceso válido.")
    app = Flask(__name__, static_folder=None)
    app.config.update(TRUSTED_HOSTS=["localhost", "127.0.0.1"], MAX_CONTENT_LENGTH=4096)

    @app.before_request
    def exigir_https():
        # Flask depende del servidor WSGI para determinar el esquema; no confiar
        # en cabeceras reenviadas sin un proxy expresamente configurado.
        if not request.is_secure:
            abort(400)
        if (request.method == "POST" and request.path in _RUTAS_ACCESO
                and request.headers.get("Origin") == request.host_url.rstrip("/")
                and request.headers.get("Sec-Fetch-Site") != "cross-site"):
            espera = limitador.esperar_segundos()
            if espera:
                respuesta = jsonify(error=f"Hay demasiados intentos de acceso. Espera {espera} segundos y vuelve a intentarlo.")
                respuesta.status_code = 429
                respuesta.headers["Retry-After"] = str(espera)
                return respuesta

    @app.after_request
    def proteger_respuestas(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.get("/static/<path:filename>", endpoint="static")
    def recurso(filename):
        if filename not in _RECURSOS:
            abort(404)
        return send_from_directory(Path(app.root_path) / "static", filename)

    @app.get("/")
    def inicio():
        return redirect(url_for("pantalla_acceso"))

    @app.get("/acceso")
    def pantalla_acceso():
        return render_template("acceso.html")

    @app.get("/taximetro")
    @requerir_sesion(sesiones, roles={"conductor", "administrador"})
    def taximetro():
        return render_template("taximetro_seguro.html", nombre=g.identidad_taxiroute.nombre_visible,
                               rol=g.identidad_taxiroute.rol, durable=ruta_historial_durable is not None,
                               persistente=app.config.get("DEMO_PERSISTENTE", False))

    @app.get("/administracion")
    @requerir_sesion(sesiones, roles={"administrador"})
    def administracion():
        return render_template("administracion.html", persistente=app.config.get("DEMO_PERSISTENTE", False),
                               historial_flota=ruta_historial_flota is not None)
    app.register_blueprint(crear_acceso_conductor(sesiones))
    app.register_blueprint(crear_cambio_conductor(sesiones.ruta, reloj=sesiones._reloj))
    app.register_blueprint(crear_acceso_administrador(pin))
    if ruta_historial_durable is None:
        app.register_blueprint(crear_api_flota(sesiones, flota, historial_por_usuario))
    else:
        from taxiroute.api_durable_experimental import crear_api_durable_experimental
        app.register_blueprint(crear_api_durable_experimental(sesiones, flota, ruta_historial_durable, prefijo="/api"))
    app.register_blueprint(crear_administracion_web(sesiones))
    app.register_blueprint(crear_alta_conductor_web(sesiones))
    app.register_blueprint(crear_web_pendientes(sesiones))
    if ruta_historial_flota is not None:
        from taxiroute.web_historial_flota import crear_historial_flota_web
        app.register_blueprint(crear_historial_flota_web(sesiones, ruta_historial_flota))
    if ruta_tarifas is not None:
        app.register_blueprint(crear_tarifas_web(sesiones, ruta_tarifas))
    return app
