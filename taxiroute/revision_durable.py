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
        barrera = conexion.execute("SELECT revision FROM barrera_revision WHERE usuario_id=?", (usuario_id,)).fetchone()
        if barrera is not None:
            revision = barrera[0]
            if type(revision) is not int or not 0 <= revision < 2**63:
                raise RevisionDurableInvalida("La barrera de revisión no es válida.")
            mayor = revision
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
            anterior = recibo.punto
            if (punto is None or punto.revision < anterior.revision
                    or punto.usuario_id != anterior.usuario_id or punto.inicio != anterior.inicio
                    or punto.tarifa_parado != anterior.tarifa_parado
                    or punto.tarifa_movimiento != anterior.tarifa_movimiento
                    or punto.ultimo_control < anterior.ultimo_control
                    or punto.parado_ns < anterior.parado_ns or punto.movimiento_ns < anterior.movimiento_ns):
                raise RevisionDurableInvalida("Un recibo no tiene punto compatible.")
            if (punto.ultimo_control == anterior.ultimo_control and
                    (punto.estado_vehiculo != anterior.estado_vehiculo or
                     punto.parado_ns != anterior.parado_ns or punto.movimiento_ns != anterior.movimiento_ns)):
                raise RevisionDurableInvalida("El instante coincide con tiempos incompatibles.")
            if punto.revision == anterior.revision:
                # Un control periódico avanza en el mismo estado y revisión.
                otro_tramo = (punto.movimiento_ns != anterior.movimiento_ns if punto.estado_vehiculo == "parado"
                              else punto.parado_ns != anterior.parado_ns)
                if (punto.estado_vehiculo != anterior.estado_vehiculo or otro_tramo
                        or (punto.ultimo_control == anterior.ultimo_control and punto != anterior)):
                    raise RevisionDurableInvalida("El control periódico no coincide con el recibo.")
            mayor = max(mayor, recibo.punto.revision)
    except RevisionDurableInvalida:
        raise
    except (ValueError, TypeError, OverflowError, KeyError) as error:
        raise RevisionDurableInvalida("No se puede comprobar la revisión guardada.") from error
    return mayor
