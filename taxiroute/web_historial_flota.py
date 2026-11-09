"""Consulta experimental de historial de flota para administradores."""
from contextlib import closing
from pathlib import Path
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion


LIMITE_CARRERAS = 100


def crear_historial_flota_web(sesiones, ruta_historial):
    """El servidor debe configurar una ruta de historial confiable y TLS."""
    api = Blueprint("historial_flota_web", __name__, url_prefix="/api/administracion")
    ruta = Path(ruta_historial)

    @api.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @api.get("/historial")
    @requerir_sesion(sesiones, roles={"administrador"})
    def consultar():
        if not request.is_secure:
            abort(400)
        if (set(request.args) - {"periodo", "pagina"}
                or len(request.args.getlist("periodo")) > 1 or len(request.args.getlist("pagina")) > 1):
            abort(400)
        periodo = request.args.get("periodo", "hoy")
        if periodo not in {"hoy", "todas"}:
            abort(400)
        pagina_texto = request.args.get("pagina", "1")
        if not pagina_texto.isascii() or not pagina_texto.isdigit() or len(pagina_texto) > 5:
            abort(400)
        pagina = int(pagina_texto)
        if not 1 <= pagina <= 10000:
            abort(400)
        token = request.cookies.get(NOMBRE_COOKIE)
        try:
            # Historial y cuentas se leen sin mantener abiertas ambas bases a la
            # vez. La sesión se vuelve a validar antes de entregar resultados.
            uri = ruta.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)) as historial:
                historial.row_factory = sqlite3.Row
                historial.execute("BEGIN")
                HistorialSQLite._verificar(historial, permitir_creacion=False)
                condicion = "WHERE date(fin, 'localtime') = date('now', 'localtime')" if periodo == "hoy" else ""
                filas = historial.execute(
                    "SELECT inicio, fin, duracion_segundos, importe, usuario_id FROM carreras "
                    + condicion + " ORDER BY orden DESC LIMIT ? OFFSET ?",
                    (LIMITE_CARRERAS + 1, (pagina - 1) * LIMITE_CARRERAS)
                ).fetchall()
                registros = [dict(fila) for fila in filas[:LIMITE_CARRERAS]]
                hay_mas = len(filas) > LIMITE_CARRERAS
            def autorizar_y_nombres(identidad, conexion):
                ids = {fila["usuario_id"] for fila in registros if fila["usuario_id"] is not None}
                if not ids:
                    return {}
                marcadores = ",".join("?" for _ in ids)
                nombres = conexion.execute(
                    f"SELECT id, nombre_visible FROM usuarios WHERE id IN ({marcadores})", tuple(ids)
                ).fetchall()
                return {fila["id"]: fila["nombre_visible"] for fila in nombres}
            nombres = sesiones.ejecutar_con_sesion_y_conexion(token,
                roles={"administrador"}, operacion=autorizar_y_nombres)
        except SesionNoAutorizada:
            abort(401)
        except (sqlite3.Error, OSError, ValueError):
            return jsonify(error="No se puede consultar el historial de la flota."), 503
        for fila in registros:
            usuario = fila.pop("usuario_id")
            fila["conductor"] = "Sin asignar" if usuario is None else nombres.get(usuario, "Cuenta no disponible")
        return jsonify(carreras=registros, periodo=periodo, pagina=pagina, limite=LIMITE_CARRERAS,
                       hay_anterior=pagina > 1, hay_mas=hay_mas)

    return api
