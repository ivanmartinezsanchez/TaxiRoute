"""Administración interna de conductores; no expuesta como API web."""
from taxiroute.acceso import _ahora
from taxiroute.sesiones import _hash_token, _transaccion, _identidad_vigente


class AdministracionDenegada(ValueError):
    """No se autoriza la operación o la cuenta no es un conductor."""


class CarreraPendienteAdministracion(ValueError):
    """La cuenta conserva una carrera o acción que necesita atención."""


class EstadoFlotaNoDisponible(ValueError):
    """No se puede comprobar la flota compartida de manera segura."""


def administrar_conductor(ruta, token, usuario_id, accion, *, reloj=_ahora, flota=None):
    """Modifica una cuenta y registra auditoría en la misma transacción.

    El llamador no puede proporcionar el rol del actor. La identidad y permisos
    proceden exclusivamente de una sesión administrativa vigente en SQLite.
    """
    protegido = _hash_token(token)
    if protegido is None or not isinstance(usuario_id, str) or accion not in (
        "desactivar", "reactivar", "desbloquear", "revocar",
    ):
        raise AdministracionDenegada("No se puede realizar la operación.")
    with _transaccion(ruta) as c:
        ahora = reloj()
        sesion = c.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
        actor = _identidad_vigente(c, sesion, ahora)
        if actor is None or actor.rol != "administrador":
            raise AdministracionDenegada("No se puede realizar la operación.")
        afectado = c.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        if afectado is None or afectado["rol"] != "conductor":
            raise AdministracionDenegada("No se puede realizar la operación.")
        fecha = ahora.isoformat()
        if accion == "desactivar":
            # La API serializa las acciones de carrera con esta misma transacción
            # y después con el bloqueo de la flota: nunca invertir ese orden.
            from taxiroute.flota import FlotaEnMemoria
            if not isinstance(flota, FlotaEnMemoria):
                raise EstadoFlotaNoDisponible("No se puede comprobar el estado de la carrera.")
            with flota._bloqueo:
                servicio = flota._servicios.get(usuario_id)
                pendiente_memoria = (usuario_id in flota._bloqueados
                    or usuario_id in flota._cierres_preparados
                    or (servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None)))
                pendiente_base = (c.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=? LIMIT 1", (usuario_id,)).fetchone()
                    or c.execute("SELECT 1 FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente') LIMIT 1", (usuario_id,)).fetchone())
                if pendiente_memoria or pendiente_base:
                    raise CarreraPendienteAdministracion("Finaliza o revisa la carrera o acción pendiente antes de desactivar la cuenta.")
                c.execute("UPDATE usuarios SET habilitado=0, actualizado_en=? WHERE id=?", (fecha, usuario_id))
        elif accion == "reactivar":
            # Reactivar no elimina un bloqueo por intentos.
            c.execute("UPDATE usuarios SET habilitado=1, actualizado_en=? WHERE id=?", (fecha, usuario_id))
        elif accion == "desbloquear":
            # Desbloquear no reactiva una cuenta deshabilitada.
            c.execute("""UPDATE usuarios SET bloqueado_en=NULL, intentos_fallidos=0,
                         proximo_intento_en=NULL, actualizado_en=? WHERE id=?""", (fecha, usuario_id))
        if accion in ("desactivar", "revocar"):
            c.execute("UPDATE sesiones SET revocada_en=? WHERE usuario_id=? AND revocada_en IS NULL",
                      (fecha, usuario_id))
        c.execute("UPDATE sesiones SET ultima_interaccion_en=? WHERE id=?", (fecha, sesion["id"]))
        c.execute("INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha) VALUES (?, ?, ?, ?)",
                  (actor.usuario_id, usuario_id, accion, fecha))
