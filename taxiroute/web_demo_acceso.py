"""Aplicación aislada para probar la interfaz de acceso administrativo."""
from flask import Flask, g, render_template, request

from taxiroute.web_acceso import requerir_sesion
from taxiroute.web_admin_login import crear_acceso_administrador


def crear_app_demo_acceso(servicio_pin, servicio_sesiones, *, buzon_demo=None):
    """Se inyectan servicios: no inicia servidor, cuentas ni correo por sí misma."""
    app = Flask(__name__)
    app.config.update(TRUSTED_HOSTS=["localhost", "127.0.0.1"], MAX_CONTENT_LENGTH=4096)
    app.register_blueprint(crear_acceso_administrador(servicio_pin))

    @app.after_request
    def proteger_respuesta(respuesta):
        # Solo el ejecutable de demostración suministra este buzón ficticio.
        if (buzon_demo is not None and request.path == "/acceso/administrador"
                and request.method == "POST" and respuesta.status_code == 200):
            datos = respuesta.get_json(silent=True)
            if isinstance(datos, dict) and datos.get("paso") == "pin_pendiente" and buzon_demo:
                datos["codigo_demostracion"] = buzon_demo[-1]
                respuesta.set_data(app.json.dumps(datos))
        respuesta.headers["Cache-Control"] = "no-store"
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        return respuesta

    @app.get("/")
    def acceso():
        return render_template("demo_acceso.html", mostrar_datos_demo=buzon_demo is not None)

    @app.get("/panel")
    @requerir_sesion(servicio_sesiones, roles={"administrador"})
    def panel():
        return render_template("demo_panel.html", nombre=g.identidad_taxiroute.nombre_visible)

    return app
