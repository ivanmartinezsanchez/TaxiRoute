"""Historial de flota CSV: lectura coherente y autorizada para administración."""
from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
import sqlite3

from flask import Blueprint, abort, jsonify, request

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import _leer
from taxiroute.historial import _fila
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import SesionNoAutorizada
from taxiroute.web_acceso import NOMBRE_COOKIE, requerir_sesion
from taxiroute.web_historial_flota import LIMITE_CARRERAS


def crear_historial_flota_csv_web(sesiones, carpeta_historial):
    api = Blueprint("historial_flota_csv_web", __name__, url_prefix="/api/administracion")
    carpeta = Path(carpeta_historial)

    @api.after_request
    def sin_cache(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @api.get("/historial")
    @requerir_sesion(sesiones, roles={"administrador"})
    def consultar():
        if not request.is_secure:
            abort(400)
        if (set(request.args) - {"periodo", "pagina"} or
                any(len(request.args.getlist(c)) > 1 for c in ("periodo", "pagina"))):
            abort(400)
        periodo = request.args.get("periodo", "hoy")
        texto = request.args.get("pagina", "1")
        if (periodo not in {"hoy", "todas"} or not texto.isascii() or
                not texto.isdigit() or len(texto) > 5 or not 1 <= int(texto) <= 10000):
            abort(400)
        pagina = int(texto)
        token = request.cookies.get(NOMBRE_COOKIE)

        def cuentas(identidad, conexion):
            return tuple((f["id"], f["nombre_visible"]) for f in conexion.execute(
                "SELECT id, nombre_visible FROM usuarios ORDER BY id"))

        try:
            if carpeta.is_symlink() or not carpeta.is_dir():
                raise ValueError("Carpeta no disponible")
            iniciales = sesiones.ejecutar_con_sesion_y_conexion(token, roles={"administrador"}, operacion=cuentas)
            historiales = {usuario: _historial_de_cuenta(carpeta, usuario) for usuario, _ in iniciales}
            with ExitStack() as bloqueos:
                for usuario, _ in iniciales:
                    bloqueos.enter_context(bloqueo_historial(historiales[usuario].ruta))

                def leer(identidad, conexion):
                    if cuentas(identidad, conexion) != iniciales:
                        raise ValueError("Las cuentas cambiaron durante la consulta")
                    if conexion.execute("SELECT 1 FROM carreras_recuperacion WHERE estado_registro='guardado_pendiente' LIMIT 1").fetchone():
                        raise ValueError("Hay un cierre pendiente")
                    hoy = sesiones._reloj().astimezone().date()
                    resultado = []
                    ids = set()
                    for usuario, nombre in iniciales:
                        historial = _historial_de_cuenta(carpeta, usuario)
                        if historial.ruta != historiales[usuario].ruta:
                            raise ValueError("Cambió la ruta del historial")
                        confirmadas = conexion.execute("SELECT id FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro='guardada'", (usuario,)).fetchall()
                        if confirmadas and not historial.ruta.is_file():
                            raise ValueError("Falta un historial confirmado")
                        if historial.ruta.exists() and os.path.samefile(sesiones.ruta, historial.ruta):
                            raise ValueError("Historial no válido")
                        filas = historial._consultar_sin_bloqueo()
                        por_id = {}
                        for fila in filas:
                            identificador = fila["identificador"]
                            if identificador in ids:
                                raise ValueError("Identificador duplicado")
                            ids.add(identificador)
                            por_id[identificador] = fila
                            fin = datetime.fromisoformat(fila["fin"])
                            if fin.utcoffset() is None:
                                raise ValueError("Fecha sin zona horaria")
                            if periodo == "todas" or fin.astimezone().date() == hoy:
                                resultado.append((fin.timestamp(), identificador, {
                                    "inicio": fila["inicio"], "fin": fila["fin"],
                                    "duracion_segundos": fila["duracion_segundos"],
                                    "importe": fila["importe"], "conductor": nombre}))
                        for comprobante in confirmadas:
                            _, _, resumen = _leer(conexion, usuario, comprobante["id"])
                            if por_id.get(comprobante["id"]) != _fila(resumen):
                                raise ValueError("Un cierre confirmado no coincide")
                    resultado.sort(key=lambda fila: (fila[0], fila[1]), reverse=True)
                    return resultado
                filas = sesiones.ejecutar_con_sesion_y_conexion(token, roles={"administrador"}, operacion=leer)
        except SesionNoAutorizada:
            abort(401)
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            return jsonify(error="No se puede consultar el historial de la flota."), 503
        desde = (pagina - 1) * LIMITE_CARRERAS
        return jsonify(carreras=[f[2] for f in filas[desde:desde + LIMITE_CARRERAS]],
                       periodo=periodo, pagina=pagina, limite=LIMITE_CARRERAS,
                       hay_anterior=pagina > 1, hay_mas=len(filas) > desde + LIMITE_CARRERAS)

    return api
