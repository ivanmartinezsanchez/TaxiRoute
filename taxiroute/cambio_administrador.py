"""Cambio obligatorio administrativo tras contraseña y PIN, sin crear sesión."""
from datetime import datetime
import hashlib

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from taxiroute.acceso import CambioContrasenaDenegado, _ahora, _disponible
from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import DatosCuentaInvalidos, _PROTECTOR, validar_contrasena
from taxiroute.pin import _transaccion


def _buscar(conexion, hash_token):
    return conexion.execute(
        """SELECT u.*, a.caduca_en AS autorizacion_caduca,
                  a.consumido_en AS autorizacion_consumida,
                  d.consumido_en AS pin_consumido, d.invalidado_en AS pin_invalidado,
                  d.version_credenciales AS version_pin,
                  d.correo_destino, c.correo_verificacion
           FROM autorizaciones_cambio a
           JOIN desafios_acceso d ON d.id=a.desafio_id
           JOIN usuarios u ON u.id=d.usuario_id
           JOIN configuracion_administrador c ON c.usuario_id=u.id
           WHERE a.hash_token=?""",
        (hash_token,),
    ).fetchone()


def _vigente(fila, ahora):
    return (
        fila is not None and fila["rol"] == "administrador"
        and fila["cambio_obligatorio"] and _disponible(fila, ahora)
        and fila["autorizacion_consumida"] is None
        and ahora < datetime.fromisoformat(fila["autorizacion_caduca"])
        and fila["pin_consumido"] is not None and fila["pin_invalidado"] is None
        and fila["version_pin"] == fila["version_credenciales"]
        and fila["correo_destino"] == fila["correo_verificacion"]
    )


def cambiar_contrasena_administrador(ruta, autorizacion, nueva, *, reloj=_ahora):
    """Consume una autorización aleatoria emitida por el servidor tras el PIN.

    No acepta usuario, rol ni booleano de PIN correcto aportados por un cliente.
    El transporte web futuro deberá proteger el token, el origen y la sesión
    previa al acceso. Esta función no es un endpoint ni una sesión.
    """
    if not isinstance(autorizacion, str) or not 40 <= len(autorizacion) <= 128:
        raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.")
    try:
        hash_token = hashlib.sha256(autorizacion.encode("ascii")).hexdigest()
    except UnicodeError:
        raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.") from None
    with conectar(ruta) as conexion:
        anterior = _buscar(conexion, hash_token)
    if not _vigente(anterior, reloj()):
        raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.")
    validar_contrasena(nueva)
    try:
        repetida = _PROTECTOR.verify(anterior["hash_contrasena"], nueva)
    except VerifyMismatchError:
        repetida = False
    except (InvalidHashError, VerificationError):
        raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.") from None
    if repetida:
        raise DatosCuentaInvalidos("La contraseña nueva debe ser distinta de la actual.")
    protegido = _PROTECTOR.hash(nueva)
    with _transaccion(ruta) as conexion:
        actual = _buscar(conexion, hash_token)
        ahora = reloj()
        if (
            not _vigente(actual, ahora)
            or actual["version_credenciales"] != anterior["version_credenciales"]
            or actual["hash_contrasena"] != anterior["hash_contrasena"]
        ):
            raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.")
        conexion.execute(
            """UPDATE usuarios SET hash_contrasena=?, cambio_obligatorio=0,
               version_credenciales=version_credenciales+1, actualizado_en=? WHERE id=?""",
            (protegido, ahora.isoformat(), actual["id"]),
        )
        conexion.execute(
            "UPDATE autorizaciones_cambio SET consumido_en=? WHERE hash_token=?",
            (ahora.isoformat(), hash_token),
        )
