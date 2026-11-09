"""Obtiene revisiones del protocolo experimental sin crear ni modificar datos."""
from taxiroute.puntos_control import _desde_fila


class RevisionDurableInvalida(ValueError):
    """Los puntos o recibos no permiten determinar una revisión segura."""


def revision_durable(conexion, usuario_id):
    """Máxima revisión comprobada; debe usarse bajo transacción SQLite.

    Cada recibo debe apuntar a un punto del mismo usuario de revisión al menos
    tan reciente. No afirma que otras vías ajenas al protocolo sean seguras.
    """
    from taxiroute.recibos_operacion import _recibo

    puntos = {}
    mayor = 0
    try:
        for fila in conexion.execute(
            "SELECT * FROM carreras_recuperacion WHERE usuario_id=?", (usuario_id,)
        ):
            punto = _desde_fila(fila)
            puntos[punto.carrera_id] = punto
            mayor = max(mayor, punto.revision)
        for fila in conexion.execute(
            "SELECT * FROM recibos_operacion WHERE usuario_id=?", (usuario_id,)
        ):
            recibo = _recibo(fila)
            punto = puntos.get(recibo.punto.carrera_id)
            if (punto is None or punto.revision < recibo.punto.revision
                    or (punto.revision == recibo.punto.revision and punto != recibo.punto)):
                raise RevisionDurableInvalida("Un recibo no tiene punto compatible.")
            mayor = max(mayor, recibo.punto.revision)
    except RevisionDurableInvalida:
        raise
    except (ValueError, TypeError, OverflowError, KeyError) as error:
        raise RevisionDurableInvalida("No se puede comprobar la revisión guardada.") from error
    return mayor
