"""Prototipo web local: una carrera de prueba en memoria por proceso."""
from secrets import token_urlsafe, compare_digest
from threading import RLock

from flask import Flask, jsonify, render_template, request, abort

from taxiroute.carrera import Carrera, EstadoVehiculo


def crear_app(crear_carrera=Carrera):
    """Crea una instancia aislada para ejecutar la aplicación o probarla."""
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=["127.0.0.1", "localhost"], MAX_CONTENT_LENGTH=4096)
    token = token_urlsafe(32)
    bloqueo = RLock()
    carrera = None
    revision = 0

    def estado():
        activa = carrera is not None and not carrera.finalizada
        return {
            "activa": activa,
            "estado": carrera.estado.value if activa else "sin carrera activa",
            "importe": str(carrera.importe_actual()) if carrera else "0.00",
            "finalizada": carrera is not None and carrera.finalizada,
            "revision": revision,
        }

    @app.before_request
    def proteger_operaciones():
        if request.method == "POST":
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            origen = request.headers.get("Origin")
            if origen and origen != request.host_url.rstrip("/"):
                abort(403)
            if not compare_digest(request.headers.get("X-TaxiRoute-Token", ""), token):
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
        return render_template("prototipo.html", token=token)

    @app.get("/prototipo/estado")
    def consultar():
        with bloqueo:
            return jsonify(estado())

    @app.post("/prototipo/accion")
    def actuar():
        nonlocal carrera, revision
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict) or datos.get("accion") not in {"iniciar", "mover", "parar", "finalizar"}:
            return jsonify(error="Acción no válida."), 400
        with bloqueo:
            # Evita aplicar una acción antigua o duplicada tras otra petición.
            if type(datos.get("revision")) is not int or datos["revision"] != revision:
                return jsonify(error="El estado ha cambiado. Revisa la pantalla e inténtalo de nuevo."), 409
            accion = datos["accion"]
            activa = carrera is not None and not carrera.finalizada
            if accion == "iniciar":
                if activa:
                    return jsonify(error="Ya hay una carrera activa."), 409
                carrera = crear_carrera()
                mensaje = "Carrera de prueba iniciada en parado."
            else:
                if not activa:
                    return jsonify(error="Inicia una carrera antes de realizar esta acción."), 409
                if accion == "finalizar":
                    carrera.finalizar()
                    mensaje = "Carrera finalizada. El resultado no se ha guardado."
                else:
                    nuevo = EstadoVehiculo.EN_MOVIMIENTO if accion == "mover" else EstadoVehiculo.PARADO
                    carrera.cambiar_estado(nuevo)
                    mensaje = "Estado actualizado: " + nuevo.value + "."
            revision += 1
            return jsonify(**estado(), mensaje=mensaje)

    return app


if __name__ == "__main__":
    # Servidor de desarrollo, limitado al prototipo local y sin depurador.
    crear_app().run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)
