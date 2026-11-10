"""Lectura autenticada de la carrera pendiente de la propia cuenta.

Pieza interna: no modifica ni reconstruye carreras y no expone rutas web.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
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


def consultar_estado_pendiente_propio(sesiones, token):
    """Consulta el punto y la intención de la propia cuenta sin renovarla.

    Ambas lecturas comparten la transacción que verifica la sesión. Una cuenta con
    varias carreras pendientes, o datos incoherentes, produce un error cerrado.
    No calcula ni atribuye el tiempo transcurrido después del último punto.
    Una intención no demuestra que la acción solicitada llegara a ejecutarse.
    """
    def leer_punto(identidad, conexion):
        try:
            if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
                    or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
                raise PendienteInvalida("La base de recuperación no es compatible.")
            filas = conexion.execute(
                "SELECT * FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro != 'guardada'",
                (identidad.usuario_id,),
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

    def leer(identidad, conexion):
        from taxiroute.arranque_recuperacion import ArranqueRecuperacionError, _validar_intencion
        from taxiroute.revision_durable import revision_durable

        punto = leer_punto(identidad, conexion)
        try:
            filas = conexion.execute("""SELECT i.*, u.id AS propietario_existente
                FROM intenciones_operacion AS i LEFT JOIN usuarios AS u ON u.id=i.usuario_id
                WHERE i.usuario_id=?""", (identidad.usuario_id,)).fetchall()
            if len(filas) > 1:
                raise PendienteInvalida("Hay varias intenciones pendientes para la cuenta.")
            if not filas:
                return punto, None
            intencion = _validar_intencion(filas[0])
            if intencion.accion == "iniciar":
                if punto is not None or revision_durable(conexion, identidad.usuario_id) != intencion.revision:
                    raise PendienteInvalida("La intención de inicio no coincide con el estado guardado.")
            else:
                esperado = "parado" if intencion.accion == "mover" else "en movimiento"
                if (punto is None or punto.estado != "activa"
                        or punto.punto.carrera_id != intencion.carrera_id
                        or punto.punto.revision != intencion.revision
                        or punto.punto.estado_vehiculo != esperado):
                    raise PendienteInvalida("La intención no coincide con la carrera guardada.")
            return punto, intencion.accion
        except PendienteInvalida:
            raise
        except (ArranqueRecuperacionError, sqlite3.Error, ValueError, TypeError, OverflowError) as error:
            raise PendienteInvalida("No se puede validar la intención pendiente.") from error

    return sesiones.ejecutar_con_sesion_y_conexion(token, roles={"conductor", "administrador"}, operacion=leer)


def consultar_pendiente_propia(sesiones, token) -> PendientePropia | None:
    """Conserva la consulta de carrera existente sin exponer la intención."""
    pendiente, _ = consultar_estado_pendiente_propio(sesiones, token)
    return pendiente
