"""Conciliación experimental de resúmenes pendientes con un historial CSV.

Solo coordina cooperativamente dos almacenamientos separados: no son una
transacción atómica. No autentica ni modifica carreras en memoria; quien llame
debe autorizar al usuario, proteger los archivos y coordinar otros procesos.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import sqlite3

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.finalizacion_pendiente import ResumenPendiente, resumen_para_historial
from taxiroute.historial import HistorialCSV, _fila
from taxiroute.puntos_control import PuntoControlInvalido, _desde_fila


class ConciliacionCSVError(ValueError):
    """El resumen o el historial no permiten confirmar la conciliación."""


@dataclass(frozen=True)
class ResultadoConciliacion:
    carrera_id: str
    usuario_id: str
    ya_guardada: bool


def _identificadores(usuario_id, carrera_id):
    for valor in (usuario_id, carrera_id):
        if not isinstance(valor, str) or not valor.strip() or valor != valor.strip():
            raise ConciliacionCSVError("Los identificadores no son válidos.")


def _conectar_existente(ruta):
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ConciliacionCSVError("No se encuentra la base de recuperación.")
    conexion = sqlite3.connect(ruta.resolve().as_uri() + "?mode=rw", uri=True,
                              timeout=5, isolation_level=None)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys=ON")
    return conexion


def _leer(conexion, usuario_id, carrera_id):
    if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
            or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
        raise ConciliacionCSVError("La base de recuperación no es compatible.")
    fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                            (carrera_id, usuario_id)).fetchone()
    if fila is None or fila["estado_registro"] not in {"guardado_pendiente", "guardada"}:
        raise ConciliacionCSVError("No hay un resumen pendiente o guardado para esta cuenta.")
    try:
        punto = _desde_fila(fila)
        fin = datetime.fromisoformat(fila["fin"])
        importe = Decimal(fila["importe_final"])
        resumen = resumen_para_historial(ResumenPendiente(punto, fin, importe))
    except (ValueError, TypeError, OverflowError, InvalidOperation) as error:
        raise ConciliacionCSVError("El resumen guardado necesita revisión.") from error
    # Incluye todos los valores persistidos relevantes; solo puede cambiar estado.
    datos = {clave: fila[clave] for clave in fila.keys() if clave != "estado_registro"}
    return datos, fila["estado_registro"], resumen


def _verificar_csv(historial, resumen):
    """Exige que el llamante mantenga el bloqueo del historial."""
    esperada = _fila(resumen)
    filas = historial._consultar_sin_bloqueo()
    coincidencias = [fila for fila in filas if fila["identificador"] == resumen.identificador]
    if len(coincidencias) != 1 or coincidencias[0] != esperada:
        raise ConciliacionCSVError("La carrera no coincide de forma única en el historial.")


def conciliar_pendiente_csv(ruta_base, ruta_csv, usuario_id, carrera_id):
    """Guarda y verifica en CSV antes de marcar SQLite; permite reintento.

    Si el estado ya es guardada, solo verifica la fila existente. El bloqueo
    cooperativo del CSV se mantiene hasta el commit de SQLite, en ese orden.
    Las modificaciones que ignoren el bloqueo y las posteriores al commit no
    están protegidas; ambos archivos no forman una transacción atómica.
    No borra ni repara archivos.
    """
    return _conciliar(ruta_base, ruta_csv, usuario_id, carrera_id)


def conciliar_pendiente_csv_autorizado(sesiones, token, carpeta_historial, carrera_id):
    """Concilia solo para la cuenta de una sesión válida durante la escritura.

    La carpeta procede de la configuración del servidor. El usuario y el CSV
    se eligen a partir de la sesión; ambos se vuelven a comprobar dentro de la
    transacción de cuentas, tras adquirir el bloqueo cooperativo del CSV.
    """
    from taxiroute.rutas_csv import _historial_de_cuenta
    from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente

    protegido = _hash_token(token)
    identidad = sesiones.consultar(token) if protegido is not None else None
    if identidad is None or identidad.rol not in {"administrador", "conductor"}:
        raise SesionNoAutorizada()
    carpeta = Path(carpeta_historial)
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise ConciliacionCSVError("La carpeta de historiales no está disponible.")
    historial = _historial_de_cuenta(carpeta.resolve(), identidad.usuario_id)

    def validar_sesion(conexion):
        sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
        actual = _identidad_vigente(conexion, sesion, sesiones._reloj())
        if (actual is None or actual.usuario_id != identidad.usuario_id
                or actual.rol != identidad.rol or actual.rol not in {"administrador", "conductor"}):
            raise SesionNoAutorizada()
        # Se comprueba de nuevo la ruta antes de escribir, sin tomar una ruta
        # de entrada del navegador ni aceptar enlaces en el directorio propio.
        if carpeta.is_symlink() or not carpeta.is_dir():
            raise ConciliacionCSVError("La carpeta de historiales no está disponible.")
        nueva = _historial_de_cuenta(carpeta.resolve(), actual.usuario_id)
        if nueva.ruta != historial.ruta:
            raise ConciliacionCSVError("El historial de la cuenta ha cambiado.")

    return _conciliar(sesiones.ruta, historial.ruta, identidad.usuario_id, carrera_id, validar_sesion)


def _conciliar(ruta_base, ruta_csv, usuario_id, carrera_id, validar_sesion=None):
    _identificadores(usuario_id, carrera_id)
    base, csv = Path(ruta_base), Path(ruta_csv)
    if base.resolve() == csv.resolve() or (base.exists() and csv.exists() and os.path.samefile(base, csv)):
        raise ConciliacionCSVError("La base y el historial deben ser archivos diferentes.")
    with closing(_conectar_existente(base)) as conexion:
        datos, estado, resumen = _leer(conexion, usuario_id, carrera_id)
    historial = HistorialCSV(csv)
    with bloqueo_historial(historial.ruta):
        with closing(_conectar_existente(base)) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                if validar_sesion is not None:
                    validar_sesion(conexion)
                actuales, estado_actual, _ = _leer(conexion, usuario_id, carrera_id)
                if actuales != datos:
                    raise ConciliacionCSVError("El resumen cambió durante la conciliación.")
                if estado_actual == "guardado_pendiente":
                    historial._guardar_sin_bloqueo(resumen)
                _verificar_csv(historial, resumen)
                if estado_actual == "guardado_pendiente":
                    resultado = conexion.execute("""UPDATE carreras_recuperacion SET estado_registro='guardada'
                        WHERE id=? AND usuario_id=? AND estado_registro='guardado_pendiente'""",
                        (carrera_id, usuario_id))
                    if resultado.rowcount != 1:
                        raise ConciliacionCSVError("No se pudo confirmar la carrera guardada.")
                elif estado_actual != "guardada":
                    raise ConciliacionCSVError("La carrera ya no admite confirmación.")
                conexion.commit()
            except BaseException:
                conexion.rollback()
                raise
    return ResultadoConciliacion(carrera_id, usuario_id, estado_actual == "guardada")
