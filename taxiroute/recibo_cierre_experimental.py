"""Ensayo interno de comprobante atómico de preparación de cierre.

No autentica ni se expone por HTTP. No inserta historial, libera la carrera ni
afirma que el cierre se haya guardado. Requiere base y tabla de prueba.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from taxiroute.finalizacion_pendiente import preparar_punto_final_en_transaccion, resumen_para_historial
from taxiroute.conciliacion_csv import _leer
from taxiroute.puntos_control import PuntoControl, _desde_fila, _validar
from taxiroute.recibos_operacion import _codificar


class ReciboCierreError(ValueError):
    pass


TABLA = """CREATE TABLE recibos_cierre (
    id TEXT PRIMARY KEY NOT NULL,
    usuario_id TEXT NOT NULL REFERENCES usuarios(id),
    carrera_id TEXT NOT NULL UNIQUE REFERENCES carreras_recuperacion(id),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    punto_json TEXT NOT NULL,
    creado_en TEXT NOT NULL
) STRICT"""


def consultar(conexion, *, id_operacion, usuario_id, revision):
    """Consulta un comprobante propio; el llamador debe autenticar al usuario.

    La respuesta por sí sola no confirma que la carrera exista en el historial.
    """
    if not conexion.in_transaction:
        raise ReciboCierreError("Se necesita una transacción activa.")
    try:
        if not isinstance(id_operacion, str) or str(UUID(id_operacion)) != id_operacion:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ReciboCierreError("Identificador no válido.") from None
    if not isinstance(usuario_id, str) or not usuario_id.strip() or type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise ReciboCierreError("Datos de consulta no válidos.")
    fila = conexion.execute("SELECT * FROM recibos_cierre WHERE id=?", (id_operacion,)).fetchone()
    if fila is None:
        return None
    if fila["usuario_id"] != usuario_id or fila["revision"] != revision:
        raise ReciboCierreError("La operación no coincide con el comprobante.")
    _, estado, resumen = _leer(conexion, usuario_id, fila["carrera_id"])
    punto_fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                 (fila["carrera_id"], usuario_id)).fetchone()
    if punto_fila is None or _codificar(_desde_fila(punto_fila)) != fila["punto_json"]:
        raise ReciboCierreError("La carrera no coincide con el comprobante.")
    return resumen, estado


def preparar(conexion, *, id_operacion, usuario_id, revision, punto):
    """Registra el punto final y su identificador en una transacción externa.

    El llamador debe autenticar y verificar que la captura proviene de la
    carrera viva. Si el identificador ya existe, se admite únicamente la misma
    captura y se devuelve el estado persistido; no se concilia el historial.
    """
    if not conexion.in_transaction:
        raise ReciboCierreError("Se necesita una transacción activa.")
    try:
        if not isinstance(id_operacion, str) or str(UUID(id_operacion)) != id_operacion:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ReciboCierreError("Identificador no válido.") from None
    if (not isinstance(punto, PuntoControl) or not isinstance(usuario_id, str)
            or punto.usuario_id != usuario_id or type(revision) is not int
            or not 0 <= revision < 2**63 - 1 or punto.revision != revision + 1):
        raise ReciboCierreError("La captura no coincide con la operación.")
    _validar(punto)
    contenido = _codificar(punto)
    existente = conexion.execute("SELECT * FROM recibos_cierre WHERE id=?", (id_operacion,)).fetchone()
    if existente is not None:
        if (existente["usuario_id"], existente["carrera_id"], existente["revision"], existente["punto_json"]) != (
                usuario_id, punto.carrera_id, revision, contenido):
            raise ReciboCierreError("La operación no coincide con el comprobante.")
        resumen, estado = consultar(conexion, id_operacion=id_operacion, usuario_id=usuario_id, revision=revision)
        return resumen, estado, True

    for tabla in ("intenciones_operacion", "recibos_operacion", "intenciones_descartadas"):
        if conexion.execute(f"SELECT 1 FROM {tabla} WHERE id=?", (id_operacion,)).fetchone():
            raise ReciboCierreError("El identificador ya se ha utilizado.")
    fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                            (punto.carrera_id, usuario_id)).fetchone()
    if fila is None or fila["estado_registro"] != "activa" or _desde_fila(fila).revision != revision:
        raise ReciboCierreError("No hay una carrera activa con esa revisión.")
    marca = "cierre_" + uuid4().hex
    conexion.execute(f"SAVEPOINT {marca}")
    try:
        resumen = preparar_punto_final_en_transaccion(conexion, punto, punto.ultimo_control)
        conexion.execute("""INSERT INTO recibos_cierre
            (id, usuario_id, carrera_id, revision, punto_json, creado_en) VALUES (?, ?, ?, ?, ?, ?)""",
            (id_operacion, usuario_id, punto.carrera_id, revision, contenido, datetime.now(timezone.utc).isoformat()))
        fila = conexion.execute("SELECT * FROM recibos_cierre WHERE id=?", (id_operacion,)).fetchone()
        if fila is None or (fila["usuario_id"], fila["carrera_id"], fila["revision"], fila["punto_json"]) != (
                usuario_id, punto.carrera_id, revision, contenido):
            raise ReciboCierreError("No se pudo verificar el comprobante.")
        confirmado, estado = consultar(conexion, id_operacion=id_operacion, usuario_id=usuario_id, revision=revision)
        if estado != "guardado_pendiente" or confirmado != resumen_para_historial(resumen):
            raise ReciboCierreError("El cierre y su comprobante no coinciden.")
        conexion.execute(f"RELEASE SAVEPOINT {marca}")
        return confirmado, estado, False
    except BaseException:
        if conexion.in_transaction:
            conexion.execute(f"ROLLBACK TO SAVEPOINT {marca}")
            conexion.execute(f"RELEASE SAVEPOINT {marca}")
        raise
