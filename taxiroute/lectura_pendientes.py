"""Lectura autenticada de la carrera pendiente de la propia cuenta.

Pieza interna: no modifica ni reconstruye carreras y no expone rutas web.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import sqlite3

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.finalizacion_pendiente import ResumenPendiente, resumen_para_historial
from taxiroute.puntos_control import PuntoControl, _desde_fila


class PendienteInvalida(ValueError):
    """Los datos pendientes no permiten una lectura fiable."""


@dataclass(frozen=True)
class PendientePropia:
    punto: PuntoControl
    estado: str
    fin: datetime | None = None
    importe_final: Decimal | None = None


def huella_intencion(intencion, pendiente):
    """Identifica el diagnóstico validado; no autentica ni autoriza acciones.

    Incluye la identidad interna y todos los campos de la intención y del punto
    asociado. Usa JSON canónico y fechas UTC. El hash no sustituye la sesión ni
    es un secreto; un futuro confirmador debe recalcularlo en su transacción.
    """
    from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, IntencionAlArrancar, _validar_intencion
    from taxiroute.recibos_operacion import _codificar

    try:
        if not isinstance(intencion, IntencionAlArrancar):
            raise ValueError
        datos = asdict(intencion)
        validada = _validar_intencion(datos | {"propietario_existente": intencion.usuario_id})
        if validada != intencion:
            raise ValueError
        if intencion.accion == "iniciar":
            if pendiente is not None:
                raise ValueError
            punto = None
        else:
            if (not isinstance(pendiente, PendientePropia) or pendiente.estado != "activa"
                    or pendiente.fin is not None or pendiente.importe_final is not None):
                raise ValueError
            captura = pendiente.punto
            esperado = "parado" if intencion.accion == "mover" else "en movimiento"
            if (captura.usuario_id != intencion.usuario_id or captura.carrera_id != intencion.carrera_id
                    or captura.revision != intencion.revision or captura.estado_vehiculo != esperado):
                raise ValueError
            punto = {"estado": pendiente.estado, "captura": json.loads(_codificar(captura))}
        datos["creada_en"] = datetime.fromisoformat(datos["creada_en"]).astimezone(timezone.utc).isoformat()
        contenido = json.dumps({"version": 1, "intencion": datos, "punto": punto},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return sha256(contenido.encode("utf-8")).hexdigest()
    except (ArranqueRecuperacionError, ValueError, TypeError, AttributeError, OverflowError) as error:
        raise PendienteInvalida("No se puede identificar el diagnóstico pendiente.") from error


def leer_estado_pendiente_en_transaccion(conexion, usuario_id):
    """Lee un usuario previamente autorizado, en la transacción del llamante.

    Devuelve (PendientePropia | None, IntencionAlArrancar | None). No comprueba
    permisos ni confirma transacciones. Debe utilizarse en el mismo bloqueo de
    base de la operación que autoriza y procesa los datos.
    """
    if not conexion.in_transaction or not isinstance(usuario_id, str) or not usuario_id.strip() or usuario_id != usuario_id.strip():
        raise PendienteInvalida("Se necesita una transacción y una cuenta válidas.")

    def leer_punto():
        try:
            if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
                    or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
                raise PendienteInvalida("La base de recuperación no es compatible.")
            filas = conexion.execute(
                "SELECT * FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro != 'guardada'",
                (usuario_id,),
            ).fetchall()
            if not filas:
                return None
            if len(filas) != 1:
                raise PendienteInvalida("Hay varias carreras pendientes para la cuenta.")
            fila = filas[0]
            estado = fila["estado_registro"]
            if estado not in {"activa", "revision_pendiente", "guardado_pendiente"}:
                raise PendienteInvalida("El estado de la carrera no es válido.")
            punto = _desde_fila(fila)
            if estado != "guardado_pendiente":
                if fila["fin"] is not None or fila["importe_final"] is not None:
                    raise PendienteInvalida("La carrera activa contiene datos finales.")
                return PendientePropia(punto, estado)
            fin = datetime.fromisoformat(fila["fin"])
            importe = Decimal(fila["importe_final"])
            resumen_para_historial(ResumenPendiente(punto, fin, importe))
            return PendientePropia(punto, estado, fin, importe)
        except PendienteInvalida:
            raise
        except (sqlite3.Error, ValueError, TypeError, OverflowError, InvalidOperation) as error:
            raise PendienteInvalida("No se pueden validar los datos pendientes.") from error

    from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, _validar_intencion
    from taxiroute.revision_durable import revision_durable

    punto = leer_punto()
    try:
        filas = conexion.execute("""SELECT i.*, u.id AS propietario_existente
            FROM intenciones_operacion AS i LEFT JOIN usuarios AS u ON u.id=i.usuario_id
            WHERE i.usuario_id=?""", (usuario_id,)).fetchall()
        if len(filas) > 1:
            raise PendienteInvalida("Hay varias intenciones pendientes para la cuenta.")
        if not filas:
            return punto, None
        intencion = _validar_intencion(filas[0])
        if intencion.accion == "iniciar":
            if punto is not None or revision_durable(conexion, usuario_id) != intencion.revision:
                raise PendienteInvalida("La intención de inicio no coincide con el estado guardado.")
        else:
            esperado = "parado" if intencion.accion == "mover" else "en movimiento"
            if (punto is None or punto.estado != "activa"
                    or punto.punto.carrera_id != intencion.carrera_id
                    or punto.punto.revision != intencion.revision
                    or punto.punto.estado_vehiculo != esperado):
                raise PendienteInvalida("La intención no coincide con la carrera guardada.")
        return punto, intencion
    except PendienteInvalida:
        raise
    except (ArranqueRecuperacionError, sqlite3.Error, ValueError, TypeError, OverflowError) as error:
        raise PendienteInvalida("No se puede validar la intención pendiente.") from error


def consultar_estado_pendiente_propio(sesiones, token):
    """Consulta el punto y la intención propios sin renovar la sesión.

    La intención no demuestra que la acción solicitada llegara a ejecutarse.
    No calcula ni atribuye el tiempo posterior al último punto fiable.
    """
    def leer(identidad, conexion):
        punto, intencion = leer_estado_pendiente_en_transaccion(conexion, identidad.usuario_id)
        diagnostico = None if intencion is None else {
            "accion": intencion.accion, "huella": huella_intencion(intencion, punto)}
        return punto, diagnostico
    return sesiones.ejecutar_con_sesion_y_conexion(token, roles={"conductor", "administrador"}, operacion=leer)


def consultar_pendiente_propia(sesiones, token) -> PendientePropia | None:
    """Conserva la consulta de carrera existente sin exponer la intención."""
    pendiente, _ = consultar_estado_pendiente_propio(sesiones, token)
    return pendiente
