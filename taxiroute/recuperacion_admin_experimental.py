"""Recuperación administrativa interna; no habilita una ruta web.

El llamador debe proteger el acceso local y aplicar límites globales antes de
exponerla. No envía correos, no emite sesiones y no recupera contraseñas.
"""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from taxiroute.acceso import _ahora, _hash_ficticio
from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.codigos_recuperacion_experimentales import consumir_codigo_en_transaccion
from taxiroute.cuentas import DatosCuentaInvalidos, _PROTECTOR, normalizar_usuario


class RecuperacionDenegada(ValueError):
    """La cuenta no puede recuperarse con los datos o el estado actuales."""


def _denegar():
    raise RecuperacionDenegada("No se ha podido recuperar el acceso.")


def _apta(fila, ahora):
    if fila is None or fila["rol"] != "administrador" or not fila["habilitado"]:
        return False
    if fila["bloqueado_en"] is None and fila["intentos_fallidos"] < 15:
        return False
    espera = fila["proximo_intento_en"]
    return espera is None or datetime.fromisoformat(espera) <= ahora


def recuperar_administrador(ruta, nombre_usuario, contrasena, codigo, *, reloj=_ahora):
    """Desbloquea con contraseña y código, revocando sesiones y desafíos.

    Una operación confirmada registra actor y afectado iguales con acción
    'desbloquear'. No inicia sesión: se debe volver al acceso normal con PIN.
    Los errores de autenticación comparten un mensaje y aplican una espera de
    60 segundos por cuenta; los errores de almacenamiento revierten el cambio.
    """
    ruta = Path(ruta)
    if ruta.is_symlink() or not ruta.is_file():
        _denegar()
    if not isinstance(contrasena, str) or len(contrasena) > 128:
        _denegar()
    try:
        contrasena.encode("utf-8")
        _, nombre = normalizar_usuario(nombre_usuario)
    except (DatosCuentaInvalidos, UnicodeError):
        _denegar()
    uri = ruta.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)) as conexion:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys=ON")
        if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
                or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
            _denegar()
        anterior = conexion.execute("SELECT * FROM usuarios WHERE nombre_usuario_normalizado=?", (nombre,)).fetchone()
        apta = _apta(anterior, reloj())
        protegido = anterior["hash_contrasena"] if apta else _hash_ficticio()
        try:
            correcta = _PROTECTOR.verify(protegido, contrasena)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            correcta = False
        if not apta:
            _denegar()
        conexion.execute("BEGIN IMMEDIATE")
        try:
            actual = conexion.execute("SELECT * FROM usuarios WHERE id=?", (anterior["id"],)).fetchone()
            ahora = reloj()
            if (not _apta(actual, ahora) or actual["nombre_usuario_normalizado"] != nombre
                    or actual["hash_contrasena"] != anterior["hash_contrasena"]
                    or actual["version_credenciales"] != anterior["version_credenciales"]):
                conexion.rollback()
                _denegar()
            valido = correcta and consumir_codigo_en_transaccion(conexion, actual["id"], codigo, ahora=ahora)
            fecha = ahora.isoformat()
            if not valido:
                fallos = min(actual["intentos_fallidos"] + 1, 2**31 - 1)
                conexion.execute("UPDATE usuarios SET intentos_fallidos=?, proximo_intento_en=?, actualizado_en=? WHERE id=?",
                    (fallos, (ahora + timedelta(seconds=60)).isoformat(), fecha, actual["id"]))
                conexion.commit()
                _denegar()
            conexion.execute("""UPDATE usuarios SET bloqueado_en=NULL, intentos_fallidos=0,
                proximo_intento_en=NULL, version_credenciales=version_credenciales+1, actualizado_en=? WHERE id=?""",
                (fecha, actual["id"]))
            conexion.execute("UPDATE sesiones SET revocada_en=? WHERE usuario_id=? AND revocada_en IS NULL",
                             (fecha, actual["id"]))
            conexion.execute("""UPDATE desafios_acceso SET invalidado_en=? WHERE usuario_id=?
                AND consumido_en IS NULL AND invalidado_en IS NULL""", (fecha, actual["id"]))
            conexion.execute("INSERT INTO auditoria_cuentas(actor_id, afectado_id, accion, fecha) VALUES (?, ?, 'desbloquear', ?)",
                             (actual["id"], actual["id"], fecha))
            conexion.commit()
        except BaseException:
            if conexion.in_transaction:
                conexion.rollback()
            raise
