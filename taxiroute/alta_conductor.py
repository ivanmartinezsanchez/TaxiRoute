"""Alta administrativa experimental; no expuesta por sí misma a la web."""
import sqlite3
import unicodedata
from uuid import uuid4

from taxiroute.acceso import _ahora
from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import (
    CuentaCreada, DatosCuentaInvalidos, UsuarioDuplicado, _PROTECTOR,
    normalizar_usuario, validar_contrasena,
)
from taxiroute.sesiones import _hash_token, _identidad_vigente, _transaccion


class AltaDenegada(ValueError):
    """La sesión no puede autorizar el alta."""


def _administrador(conexion, protegido, ahora):
    sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
    identidad = _identidad_vigente(conexion, sesion, ahora)
    if identidad is None or identidad.rol != "administrador":
        raise AltaDenegada("No se puede realizar el alta.")
    return identidad


def alta_conductor(ruta, token, *, nombre_usuario, nombre_visible, contrasena, reloj=_ahora):
    """Da de alta solo conductores; comprueba permisos antes y después del hash.

    La cuenta requiere cambio obligatorio. El alta y su auditoría comparten
    la misma transacción; el cliente no decide el actor ni el rol.
    """
    protegido = _hash_token(token)
    if protegido is None:
        raise AltaDenegada("No se puede realizar el alta.")
    # Rechazar sesiones no autorizadas antes del costoso cálculo de Argon2.
    with conectar(ruta) as conexion:
        _administrador(conexion, protegido, reloj())
    nombre, normalizado = normalizar_usuario(nombre_usuario)
    if not isinstance(nombre_visible, str):
        raise DatosCuentaInvalidos("El nombre visible debe ser texto.")
    visible = unicodedata.normalize("NFC", nombre_visible).strip()
    if not 1 <= len(visible) <= 100 or any(unicodedata.category(c).startswith("C") for c in visible):
        raise DatosCuentaInvalidos("El nombre visible debe tener entre 1 y 100 caracteres sin controles.")
    validar_contrasena(contrasena)
    hash_contrasena = _PROTECTOR.hash(contrasena)
    identificador = str(uuid4())
    try:
        with _transaccion(ruta) as conexion:
            ahora = reloj()
            actor = _administrador(conexion, protegido, ahora)
            fecha = ahora.isoformat()
            conexion.execute("""INSERT INTO usuarios
                (id, nombre_usuario, nombre_usuario_normalizado, nombre_visible, rol,
                 hash_contrasena, cambio_obligatorio, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, 'conductor', ?, 1, ?, ?)""",
                (identificador, nombre, normalizado, visible, hash_contrasena, fecha, fecha))
            conexion.execute("UPDATE sesiones SET ultima_interaccion_en=? WHERE hash_token=?",
                             (fecha, protegido))
            conexion.execute("""INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha)
                VALUES (?, ?, 'alta', ?)""", (actor.usuario_id, identificador, fecha))
    except sqlite3.IntegrityError as error:
        if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
            raise UsuarioDuplicado("Ese nombre de usuario ya existe.") from None
        raise
    return CuentaCreada(identificador, nombre, visible, "conductor", True)