"""Primitivas internas para códigos de recuperación; no autorizan accesos.

Su tabla forma parte de la base de cuentas desde la versión 11. Cualquier
flujo debe comprobar la contraseña, limitar intentos y consumir el código,
desbloquear, revocar sesiones y auditar dentro de la misma transacción.
"""
import hashlib
import re
import secrets
from datetime import datetime, timezone


CREAR_TABLA_CODIGOS = """CREATE TABLE codigos_recuperacion (
    usuario_id TEXT NOT NULL REFERENCES usuarios(id),
    verificador TEXT NOT NULL UNIQUE CHECK(length(verificador) = 64),
    creado_en TEXT NOT NULL,
    usado_en TEXT,
    invalidado_en TEXT,
    PRIMARY KEY (usuario_id, verificador)
) STRICT"""

_FORMATO = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


def _requiere_transaccion(conexion):
    if not conexion.in_transaction:
        raise ValueError("Se necesita una transacción activa.")


def _verificador(usuario_id, codigo):
    contenido = b"TaxiRoute-recuperacion-v1\0" + usuario_id.encode("utf-8") + b"\0" + codigo.encode("ascii")
    return hashlib.sha256(contenido).hexdigest()


def _fecha(ahora):
    instante = datetime.now(timezone.utc) if ahora is None else ahora
    if not isinstance(instante, datetime) or instante.tzinfo is None or instante.utcoffset() is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return instante.astimezone(timezone.utc).isoformat()


def generar_codigos_en_transaccion(conexion, usuario_id, *, cantidad=5, ahora=None):
    """Crea un primer lote para un administrador; mostrar solo tras confirmar.

    El llamador debe autorizar el alta y confirmar la transacción antes de
    mostrar los códigos. No permite regenerar ni reemplazar lotes existentes.
    """
    _requiere_transaccion(conexion)
    if not isinstance(usuario_id, str) or not usuario_id.strip() or type(cantidad) is not int or not 1 <= cantidad <= 10:
        raise ValueError("Los datos de generación no son válidos.")
    usuario = conexion.execute("SELECT rol FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
    if usuario is None or usuario[0] != "administrador":
        raise ValueError("Se necesita una cuenta administradora existente.")
    if conexion.execute("SELECT 1 FROM codigos_recuperacion WHERE usuario_id=? LIMIT 1", (usuario_id,)).fetchone():
        raise ValueError("La cuenta ya tiene códigos; no se sustituirán.")
    fecha = _fecha(ahora)
    codigos = tuple(secrets.token_urlsafe(32) for _ in range(cantidad))
    conexion.executemany("""INSERT INTO codigos_recuperacion (usuario_id, verificador, creado_en)
        VALUES (?, ?, ?)""", [(usuario_id, _verificador(usuario_id, codigo), fecha) for codigo in codigos])
    return codigos


def consumir_codigo_en_transaccion(conexion, usuario_id, codigo, *, ahora=None):
    """Consume un código válido, sin verificar contraseña ni conceder sesión.

    Solo un flujo autorizado puede llamar a esta función. El consumo y todas
    las acciones de recuperación deben confirmarse o revertirse juntos.
    """
    _requiere_transaccion(conexion)
    if not isinstance(usuario_id, str) or not usuario_id.strip() or not isinstance(codigo, str) or not _FORMATO.fullmatch(codigo):
        return False
    fecha = _fecha(ahora)
    resultado = conexion.execute("""UPDATE codigos_recuperacion SET usado_en=?
        WHERE usuario_id=? AND verificador=? AND usado_en IS NULL AND invalidado_en IS NULL""",
        (fecha, usuario_id, _verificador(usuario_id, codigo)))
    return resultado.rowcount == 1
