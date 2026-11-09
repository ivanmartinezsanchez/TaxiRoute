"""Restablecimiento administrativo interno de contraseñas de conductores."""
from taxiroute.acceso import _ahora
from taxiroute.alta_conductor import _administrador, AltaDenegada
from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import _PROTECTOR, validar_contrasena
from taxiroute.sesiones import _hash_token, _transaccion


class RestablecimientoDenegado(ValueError):
    """No se autoriza la operación o el destinatario no es conductor."""


def restablecer_contrasena_conductor(ruta, token, usuario_id, contrasena_temporal, *, reloj=_ahora):
    """Requiere administrador vigente y fuerza cambio antes del siguiente acceso.

    La clave temporal se recibe por un canal elegido por el llamador; esta
    función no la comunica ni la guarda en texto claro. El bloqueo y el estado
    de habilitación se conservan y se administran por separado.
    """
    protegido = _hash_token(token)
    if protegido is None or not isinstance(usuario_id, str) or not usuario_id:
        raise RestablecimientoDenegado("No se puede restablecer la contraseña.")
    try:
        with conectar(ruta) as conexion:
            _administrador(conexion, protegido, reloj())
            afectado = conexion.execute("SELECT rol FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if afectado is None or afectado["rol"] != "conductor":
                raise RestablecimientoDenegado("No se puede restablecer la contraseña.")
        validar_contrasena(contrasena_temporal)
        hash_nuevo = _PROTECTOR.hash(contrasena_temporal)
        with _transaccion(ruta) as conexion:
            ahora = reloj()
            actor = _administrador(conexion, protegido, ahora)
            afectado = conexion.execute("SELECT rol FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if afectado is None or afectado["rol"] != "conductor":
                raise RestablecimientoDenegado("No se puede restablecer la contraseña.")
            fecha = ahora.isoformat()
            conexion.execute("""UPDATE usuarios SET hash_contrasena=?, cambio_obligatorio=1,
                version_credenciales=version_credenciales+1, actualizado_en=? WHERE id=?""",
                (hash_nuevo, fecha, usuario_id))
            conexion.execute("""UPDATE sesiones SET revocada_en=?
                WHERE usuario_id=? AND revocada_en IS NULL""", (fecha, usuario_id))
            conexion.execute("UPDATE sesiones SET ultima_interaccion_en=? WHERE hash_token=?", (fecha, protegido))
            conexion.execute("""INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha)
                VALUES (?, ?, 'restablecer_contrasena', ?)""", (actor.usuario_id, usuario_id, fecha))
    except AltaDenegada:
        raise RestablecimientoDenegado("No se puede restablecer la contraseña.") from None
