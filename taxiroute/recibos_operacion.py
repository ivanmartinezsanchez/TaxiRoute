"""Recibos experimentales; todavía no conectados a la confirmación ni a web.

El integrador debe guardar el recibo en la misma transacción que el punto y la
eliminación de la intención. Un recibo aislado no demuestra esa coordinación.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from taxiroute.intenciones_operacion import IntencionNoPermitida, _validar_base
from taxiroute.puntos_control import PuntoControl, _desde_fila, _validar
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


@dataclass(frozen=True)
class ReciboOperacion:
    id: str
    usuario_id: str
    accion: str
    revision: int
    punto: PuntoControl
    creado_en: str


def _validar_id(valor):
    try:
        if not isinstance(valor, str) or str(UUID(valor)) != valor:
            raise ValueError
    except (ValueError, AttributeError):
        raise IntencionNoPermitida("Identificador de operación no válido.")


def _codificar(punto):
    _validar(punto)
    datos = asdict(punto)
    for clave in ("inicio", "ultimo_control"):
        datos[clave] = datos[clave].astimezone(timezone.utc).isoformat()
    return json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _recibo(fila):
    try:
        datos = json.loads(fila["punto_json"])
        if not isinstance(datos, dict):
            raise ValueError
        for clave in ("inicio", "ultimo_control"):
            datos[clave] = datetime.fromisoformat(datos[clave])
        punto = PuntoControl(**datos)
        _validar(punto)
        fecha = datetime.fromisoformat(fila["creado_en"])
        if (fecha.tzinfo is None or fecha.utcoffset() is None
                or punto.usuario_id != fila["usuario_id"]
                or punto.revision != fila["revision"] + 1
                or fila["accion"] not in {"iniciar", "mover", "parar"}
                or punto.estado_vehiculo != ("en movimiento" if fila["accion"] == "mover" else "parado")
                or _codificar(punto) != fila["punto_json"]):
            raise ValueError
        _validar_id(fila["id"])
        return ReciboOperacion(fila["id"], fila["usuario_id"], fila["accion"],
                               fila["revision"], punto, fila["creado_en"])
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise IntencionNoPermitida("El recibo guardado necesita revisión.") from error


def guardar_recibo_en_transaccion(conexion, *, id_intencion, usuario_id, accion, revision, punto, creado_en):
    """Inserta o compara un recibo, sin confirmar la transacción del llamante.

    Requiere una transacción de escritura serializada y sesión ya autorizada.
    Si aún no hay recibo, exige intención y punto persistido coincidentes. El
    llamante debe validar la transición y eliminar la intención antes del commit.
    """
    if not conexion.in_transaction:
        raise IntencionNoPermitida("Se necesita una transacción activa.")
    _validar_base(conexion)
    _validar_id(id_intencion)
    if (accion not in {"iniciar", "mover", "parar"} or type(revision) is not int
            or not 0 <= revision < 2**63 - 1 or not isinstance(punto, PuntoControl)
            or not isinstance(creado_en, datetime) or creado_en.tzinfo is None
            or creado_en.utcoffset() is None):
        raise IntencionNoPermitida("Datos de recibo no válidos.")
    codificado = _codificar(punto)
    if punto.usuario_id != usuario_id or punto.revision != revision + 1:
        raise IntencionNoPermitida("El punto no corresponde al usuario o a la revisión.")
    existente = conexion.execute("SELECT * FROM recibos_operacion WHERE id=?", (id_intencion,)).fetchone()
    if existente is not None:
        recibo = _recibo(existente)
        if (recibo.usuario_id, recibo.accion, recibo.revision, _codificar(recibo.punto)) != (
                usuario_id, accion, revision, codificado):
            raise IntencionNoPermitida("El identificador ya corresponde a otro resultado.")
        return recibo
    intencion = conexion.execute("SELECT * FROM intenciones_operacion WHERE id=?", (id_intencion,)).fetchone()
    if (intencion is None or (intencion["usuario_id"], intencion["accion"], intencion["revision"]) !=
            (usuario_id, accion, revision)):
        raise IntencionNoPermitida("No hay una intención coincidente.")
    if ((accion == "iniciar" and (intencion["carrera_id"] is not None or punto.estado_vehiculo != "parado"))
            or (accion in {"mover", "parar"} and
                (intencion["carrera_id"] != punto.carrera_id or punto.estado_vehiculo !=
                 ("en movimiento" if accion == "mover" else "parado")))):
        raise IntencionNoPermitida("El punto no coincide con la intención.")
    guardado = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                               (punto.carrera_id, usuario_id)).fetchone()
    if guardado is None or guardado["estado_registro"] != "activa" or _codificar(_desde_fila(guardado)) != codificado:
        raise IntencionNoPermitida("El punto todavía no coincide con el almacenado.")
    fecha = creado_en.astimezone(timezone.utc).isoformat()
    conexion.execute("""INSERT INTO recibos_operacion
        (id, usuario_id, accion, revision, punto_json, creado_en) VALUES (?, ?, ?, ?, ?, ?)""",
        (id_intencion, usuario_id, accion, revision, codificado, fecha))
    fila = conexion.execute("SELECT * FROM recibos_operacion WHERE id=?", (id_intencion,)).fetchone()
    if fila is None:
        raise IntencionNoPermitida("No se pudo verificar el recibo.")
    recibo = _recibo(fila)
    if (recibo.usuario_id, recibo.accion, recibo.revision, _codificar(recibo.punto), recibo.creado_en) != (
            usuario_id, accion, revision, codificado, fecha):
        raise IntencionNoPermitida("El recibo guardado no coincide.")
    return recibo


def consultar_recibo(sesiones, token, id_intencion):
    """Consulta solo recibos propios con sesión vigente; None si no existe."""
    _validar_id(id_intencion)
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
        conexion.execute("BEGIN IMMEDIATE")
        try:
            _validar_base(conexion)
            sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
            identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
            if identidad is None or identidad.rol not in {"conductor", "administrador"}:
                raise SesionNoAutorizada()
            fila = conexion.execute("SELECT * FROM recibos_operacion WHERE id=? AND usuario_id=?",
                                    (id_intencion, identidad.usuario_id)).fetchone()
            resultado = _recibo(fila) if fila is not None else None
            conexion.rollback()
            return resultado
        except BaseException:
            conexion.rollback()
            raise
    finally:
        conexion.close()
