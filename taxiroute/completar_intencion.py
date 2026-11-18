"""Confirmación experimental de una intención y un punto, sin tocar la flota.

La operación conserva un recibo para reconocer reintentos idénticos después
de un commit con resultado incierto.
"""
from pathlib import Path
import sqlite3
from uuid import UUID

from taxiroute.intenciones_operacion import IntencionNoPermitida, _validar_base
from taxiroute.puntos_control import (
    PuntoControl, _desde_fila, _guardar_punto_en_transaccion, _validar,
)
from taxiroute.recibos_operacion import _codificar, _recibo, guardar_recibo_en_transaccion
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def completar_intencion(sesiones, token, id_intencion, punto):
    """Guarda un punto y elimina la intención en una única transacción.

    No modifica ni valida el estado en memoria. El integrador deberá coordinar
    la captura con el bloqueo de flota y respetar el orden SQLite -> flota.
    Un recibo propio con punto idéntico permite reconocer un reintento.
    """
    try:
        if not isinstance(id_intencion, str) or str(UUID(id_intencion)) != id_intencion:
            raise ValueError
    except (ValueError, AttributeError):
        raise IntencionNoPermitida("El identificador de intención no es válido.")
    if not isinstance(punto, PuntoControl):
        raise IntencionNoPermitida("Se necesita un punto de carrera válido.")
    _validar(punto)
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    try:
        conexion = sqlite3.connect(Path(sesiones.ruta).resolve().as_uri() + "?mode=rw",
                                  uri=True, timeout=5, isolation_level=None)
    except sqlite3.Error as error:
        raise IntencionNoPermitida("No se puede abrir la base existente.") from error
    try:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys=ON")
        conexion.execute("BEGIN IMMEDIATE")
        try:
            _validar_base(conexion)
            sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
            ahora = sesiones._reloj()
            identidad = _identidad_vigente(conexion, sesion, ahora)
            if identidad is None or identidad.rol not in {"conductor", "administrador"}:
                raise SesionNoAutorizada()
            recibo_fila = conexion.execute(
                "SELECT * FROM recibos_operacion WHERE id=? AND usuario_id=?",
                (id_intencion, identidad.usuario_id)).fetchone()
            if recibo_fila is not None:
                recibo = _recibo(recibo_fila)
                if (punto.usuario_id != identidad.usuario_id
                        or _codificar(recibo.punto) != _codificar(punto)):
                    raise IntencionNoPermitida("El identificador corresponde a otro resultado.")
                if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE id=?",
                                    (id_intencion,)).fetchone():
                    raise IntencionNoPermitida("La operación necesita revisión.")
                conexion.rollback()
                return recibo.punto
            intencion = conexion.execute(
                "SELECT * FROM intenciones_operacion WHERE id=? AND usuario_id=?",
                (id_intencion, identidad.usuario_id)).fetchone()
            if intencion is None:
                raise IntencionNoPermitida("No se encuentra la intención: se necesita conciliación.")
            accion, revision = intencion["accion"], intencion["revision"]
            if punto.usuario_id != identidad.usuario_id or punto.revision != revision + 1:
                raise IntencionNoPermitida("El propietario o la revisión del punto no coincide.")
            if accion == "iniciar":
                if intencion["carrera_id"] is not None or revision != 0 or punto.estado_vehiculo != "parado":
                    raise IntencionNoPermitida("El punto no corresponde a un inicio válido.")
                anterior = conexion.execute("""SELECT id FROM carreras_recuperacion WHERE usuario_id=?
                    AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')""",
                    (identidad.usuario_id,)).fetchone()
                if anterior is not None:
                    raise IntencionNoPermitida("La cuenta ya tiene una carrera sin resolver.")
            elif accion in {"mover", "parar"}:
                esperado = "parado" if accion == "mover" else "en movimiento"
                nuevo = "en movimiento" if accion == "mover" else "parado"
                anterior = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                             (intencion["carrera_id"], identidad.usuario_id)).fetchone()
                if (anterior is None or anterior["estado_registro"] != "activa"
                        or punto.carrera_id != intencion["carrera_id"]):
                    raise IntencionNoPermitida("La carrera ya no está activa.")
                previo = _desde_fila(anterior)
                if previo.revision != revision or previo.estado_vehiculo != esperado or punto.estado_vehiculo != nuevo:
                    raise IntencionNoPermitida("La revisión o la transición no coinciden.")
            else:
                raise IntencionNoPermitida("La acción de la intención no es válida.")
            _guardar_punto_en_transaccion(conexion, punto)
            fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                    (punto.carrera_id, identidad.usuario_id)).fetchone()
            if fila is None or fila["estado_registro"] != "activa" or _desde_fila(fila) != punto:
                raise IntencionNoPermitida("No se pudo verificar el punto guardado.")
            guardar_recibo_en_transaccion(conexion, id_intencion=id_intencion,
                usuario_id=identidad.usuario_id, accion=accion, revision=revision,
                punto=punto, creado_en=ahora)
            eliminado = conexion.execute("DELETE FROM intenciones_operacion WHERE id=? AND usuario_id=?",
                                         (id_intencion, identidad.usuario_id))
            if eliminado.rowcount != 1 or conexion.execute(
                    "SELECT 1 FROM intenciones_operacion WHERE id=?", (id_intencion,)).fetchone():
                raise IntencionNoPermitida("No se pudo confirmar la intención.")
            final = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                     (punto.carrera_id, identidad.usuario_id)).fetchone()
            recibo_final = conexion.execute("SELECT * FROM recibos_operacion WHERE id=? AND usuario_id=?",
                                            (id_intencion, identidad.usuario_id)).fetchone()
            if (final is None or final["estado_registro"] != "activa" or _desde_fila(final) != punto
                    or recibo_final is None or recibo_final["accion"] != accion
                    or recibo_final["revision"] != revision
                    or _codificar(_recibo(recibo_final).punto) != _codificar(punto)):
                raise IntencionNoPermitida("La operación cambió antes de confirmarse.")
            conexion.commit()
            return punto
        except BaseException:
            conexion.rollback()
            raise
    finally:
        conexion.close()
