"""Registro experimental de intenciones, sin ejecutar operaciones de flota.

Registrar una intención no cambia la carrera; queda pendiente hasta que un
futuro protocolo de recuperación la resuelva. La aplicación existente todavía
no la consulta ni la respeta. No usar como protección de la interfaz actual.
"""
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
import sqlite3

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.puntos_control import _desde_fila
from taxiroute.sesiones import _hash_token, _identidad_vigente, SesionNoAutorizada


class IntencionNoPermitida(ValueError):
    """La intención está en conflicto o no coincide con la carrera."""


@dataclass(frozen=True)
class Intencion:
    id: str
    usuario_id: str
    accion: str
    revision: int
    carrera_id: str | None
    creada_en: str


def _validar_base(conexion):
    if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
            or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
        raise IntencionNoPermitida("La base no es compatible con el registro de intenciones.")


def registrar_intencion(sesiones, token, *, id_intencion, accion, revision, carrera_id=None):
    """Registra una intención idempotente con sesión y revisión durables.

    El llamador genera y conserva un UUID por operación ANTES de invocar la
    función; si el commit devuelve un error incierto puede reintentar con el
    mismo UUID. Para iniciar exige la revisión durable anterior de la cuenta.
    No cancela ni elimina registros.
    """
    try:
        if not isinstance(id_intencion, str) or str(UUID(id_intencion)) != id_intencion:
            raise ValueError
    except (ValueError, AttributeError):
        raise IntencionNoPermitida("El identificador de intención debe ser un UUID canónico.")
    if accion not in {"iniciar", "mover", "parar"} or type(revision) is not int or revision < 0 or revision >= 2**63 - 1:
        raise IntencionNoPermitida("Acción o revisión no válida.")
    if accion == "iniciar":
        if carrera_id is not None:
            raise IntencionNoPermitida("El inicio no admite identificador de carrera.")
    elif not isinstance(carrera_id, str) or not carrera_id.strip() or carrera_id != carrera_id.strip():
        raise IntencionNoPermitida("Se necesita un identificador de carrera válido.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    ruta = Path(sesiones.ruta)
    try:
        conexion = sqlite3.connect(ruta.resolve().as_uri() + "?mode=rw", uri=True,
                                  timeout=5, isolation_level=None)
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
            if conexion.execute("SELECT 1 FROM recibos_operacion WHERE id=?", (id_intencion,)).fetchone():
                raise IntencionNoPermitida("El identificador corresponde a una operación terminada.")
            anterior = conexion.execute("SELECT * FROM intenciones_operacion WHERE id=?", (id_intencion,)).fetchone()
            if anterior is not None:
                if (anterior["usuario_id"], anterior["accion"], anterior["revision"], anterior["carrera_id"]) != (
                        identidad.usuario_id, accion, revision, carrera_id):
                    raise IntencionNoPermitida("El identificador ya corresponde a otra intención.")
                resultado = Intencion(*(anterior[campo] for campo in
                    ("id", "usuario_id", "accion", "revision", "carrera_id", "creada_en")))
            else:
                if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?",
                                    (identidad.usuario_id,)).fetchone():
                    raise IntencionNoPermitida("La cuenta tiene una intención pendiente de revisión.")
                fila = conexion.execute("""SELECT * FROM carreras_recuperacion WHERE usuario_id=?
                    AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')""",
                    (identidad.usuario_id,)).fetchone()
                if accion == "iniciar":
                    if fila is not None:
                        raise IntencionNoPermitida("La cuenta ya tiene una carrera sin resolver.")
                    from taxiroute.revision_durable import revision_durable
                    if revision_durable(conexion, identidad.usuario_id) != revision:
                        raise IntencionNoPermitida("La revisión durable ha cambiado.")
                else:
                    if fila is None or fila["estado_registro"] != "activa":
                        raise IntencionNoPermitida("No hay una carrera activa disponible.")
                    punto = _desde_fila(fila)
                    esperado = "parado" if accion == "mover" else "en movimiento"
                    if punto.carrera_id != carrera_id or punto.revision != revision or punto.estado_vehiculo != esperado:
                        raise IntencionNoPermitida("La revisión, carrera o estado no coincide.")
                fecha = ahora.isoformat()
                conexion.execute("""INSERT INTO intenciones_operacion
                    (id, usuario_id, accion, revision, carrera_id, creada_en)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (id_intencion, identidad.usuario_id, accion, revision, carrera_id, fecha))
                guardada = conexion.execute(
                    "SELECT * FROM intenciones_operacion WHERE id=?", (id_intencion,)
                ).fetchone()
                if guardada is None or tuple(guardada[campo] for campo in
                    ("id", "usuario_id", "accion", "revision", "carrera_id", "creada_en")) != (
                        id_intencion, identidad.usuario_id, accion, revision, carrera_id, fecha):
                    raise IntencionNoPermitida("No se pudo verificar la intención registrada.")
                resultado = Intencion(id_intencion, identidad.usuario_id, accion, revision, carrera_id, fecha)
            conexion.commit()
            return resultado
        except BaseException:
            conexion.rollback()
            raise
    finally:
        conexion.close()
