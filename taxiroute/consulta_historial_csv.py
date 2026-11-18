"""Lectura experimental de CSV con comprobación de cierres durables."""
from contextlib import closing
import os
from pathlib import Path

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import ConciliacionCSVError, _conectar_existente, _leer
from taxiroute.historial import _fila
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def consultar_historial_csv_verificado(sesiones, token, carpeta_historial):
    """Lee la cuenta de la sesión y verifica sus carreras confirmadas.

    No identifica por sí misma la instalación ni certifica filas anteriores
    que carezcan de comprobante durable. El bloqueo solo coordina procesos que
    lo respeten; los archivos CSV y SQLite no forman una transacción conjunta.
    """
    protegido = _hash_token(token)
    identidad = sesiones.consultar(token) if protegido is not None else None
    if identidad is None or identidad.rol not in {"administrador", "conductor"}:
        raise SesionNoAutorizada()
    carpeta = Path(carpeta_historial)
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise ConciliacionCSVError("La carpeta de historiales no está disponible.")
    historial = _historial_de_cuenta(carpeta.resolve(), identidad.usuario_id)
    base, csv = Path(sesiones.ruta), historial.ruta
    if base.resolve() == csv.resolve() or (base.exists() and csv.exists() and os.path.samefile(base, csv)):
        raise ConciliacionCSVError("La base y el historial deben ser archivos diferentes.")

    with bloqueo_historial(csv):
        with closing(_conectar_existente(base)) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                def comprobar_sesion():
                    sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                    actual = _identidad_vigente(conexion, sesion, sesiones._reloj())
                    if (actual is None or actual.usuario_id != identidad.usuario_id
                            or actual.rol != identidad.rol or actual.rol not in {"administrador", "conductor"}):
                        raise SesionNoAutorizada()

                comprobar_sesion()
                if carpeta.is_symlink() or not carpeta.is_dir():
                    raise ConciliacionCSVError("La carpeta de historiales no está disponible.")
                if _historial_de_cuenta(carpeta.resolve(), identidad.usuario_id).ruta != csv:
                    raise ConciliacionCSVError("El historial de la cuenta ha cambiado.")
                if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
                        or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
                    raise ConciliacionCSVError("La base de recuperación no es compatible.")
                pendientes = conexion.execute(
                    "SELECT 1 FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro='guardado_pendiente' LIMIT 1",
                    (identidad.usuario_id,)).fetchone()
                if pendientes:
                    raise ConciliacionCSVError("Hay un cierre pendiente que necesita revisión.")
                guardadas = conexion.execute(
                    "SELECT id FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro='guardada'",
                    (identidad.usuario_id,)).fetchall()
                filas = historial._consultar_sin_bloqueo()
                por_id = {}
                for fila in filas:
                    por_id.setdefault(fila["identificador"], []).append(fila)
                for registro in guardadas:
                    _, _, resumen = _leer(conexion, identidad.usuario_id, registro["id"])
                    if por_id.get(registro["id"]) != [_fila(resumen)]:
                        raise ConciliacionCSVError("Una carrera confirmada no coincide con el historial.")
                comprobar_sesion()
                conexion.commit()
                return filas
            except BaseException:
                conexion.rollback()
                raise
