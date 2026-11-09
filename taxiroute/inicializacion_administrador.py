"""Preparación interna y experimental de la primera cuenta administradora.

No se expone en la web ni crea por sí sola una instalación de uso real.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
import sqlite3
import unicodedata
from uuid import uuid4

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.codigos_recuperacion_experimentales import generar_codigos_en_transaccion
from taxiroute.cuentas import CuentaCreada, DatosCuentaInvalidos, _PROTECTOR, normalizar_usuario, validar_contrasena


class InicializacionDenegada(ValueError):
    """La base no está disponible para inicializar la primera cuenta."""


@dataclass(frozen=True)
class PreparacionConCodigos:
    """El llamador debe entregar los códigos de forma privada y evitar registrarlos."""
    cuenta: CuentaCreada
    codigos: tuple[str, ...] = field(repr=False)


def preparar_primer_administrador(ruta, *, nombre_usuario, nombre_visible, contrasena, correo):
    """Crea la cuenta y su correo en una transacción, solo en una base vacía.

    La base debe existir y estar inicializada por TaxiRoute. El llamador debe
    proteger el acceso local a este procedimiento y a la base de datos.
    La contraseña es temporal y deberá cambiarse tras la verificación del PIN.
    No envía correo, ni configura OAuth, ni inicia un servidor.
    """
    cuenta, _ = _preparar(ruta, nombre_usuario=nombre_usuario, nombre_visible=nombre_visible,
                          contrasena=contrasena, correo=correo, emitir_codigos=False)
    return cuenta


def preparar_primer_administrador_con_codigos(ruta, *, nombre_usuario, nombre_visible, contrasena, correo):
    """Crea cuenta, correo y códigos juntos; devuelve los códigos tras confirmar.

    Es una operación interna, sin interfaz de entrega ni recuperación todavía.
    La persona integradora debe entregar los códigos de forma privada; si se
    pierden, este componente aún no ofrece un procedimiento para reemplazarlos.
    """
    cuenta, codigos = _preparar(ruta, nombre_usuario=nombre_usuario, nombre_visible=nombre_visible,
                               contrasena=contrasena, correo=correo, emitir_codigos=True)
    return PreparacionConCodigos(cuenta, codigos)


def _preparar(ruta, *, nombre_usuario, nombre_visible, contrasena, correo, emitir_codigos):
    ruta = Path(ruta)
    if ruta.is_symlink() or not ruta.is_file():
        raise InicializacionDenegada("Se necesita una base de cuentas existente y preparada.")
    nombre, normalizado = normalizar_usuario(nombre_usuario)
    if not isinstance(nombre_visible, str):
        raise DatosCuentaInvalidos("El nombre visible debe ser texto.")
    visible = unicodedata.normalize("NFC", nombre_visible).strip()
    if not 1 <= len(visible) <= 100 or any(unicodedata.category(c).startswith("C") for c in visible):
        raise DatosCuentaInvalidos("El nombre visible no es válido.")
    if not isinstance(correo, str) or len(correo) > 254:
        raise DatosCuentaInvalidos("El correo no es válido.")
    nombre_correo, direccion = parseaddr(correo, strict=True)
    if nombre_correo or direccion != correo or direccion.count("@") != 1 or any(c in correo for c in "\r\n"):
        raise DatosCuentaInvalidos("El correo no es válido.")
    validar_contrasena(contrasena)
    protegido = _PROTECTOR.hash(contrasena)
    identificador = str(uuid4())
    fecha = datetime.now(timezone.utc).isoformat()
    uri = ruta.resolve().as_uri() + "?mode=rw"
    conexion = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    try:
        conexion.execute("PRAGMA foreign_keys = ON")
        conexion.execute("BEGIN IMMEDIATE")
        try:
            if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
                    or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
                raise InicializacionDenegada("La base de cuentas no es compatible.")
            if conexion.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
                raise InicializacionDenegada("La base ya contiene cuentas; no se ha modificado.")
            conexion.execute("""INSERT INTO usuarios
                (id, nombre_usuario, nombre_usuario_normalizado, nombre_visible, rol,
                 hash_contrasena, cambio_obligatorio, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, 'administrador', ?, 1, ?, ?)""",
                (identificador, nombre, normalizado, visible, protegido, fecha, fecha))
            conexion.execute("INSERT INTO configuracion_administrador (usuario_id, correo_verificacion) VALUES (?, ?)",
                             (identificador, correo))
            codigos = generar_codigos_en_transaccion(conexion, identificador) if emitir_codigos else ()
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise
    finally:
        conexion.close()
    return CuentaCreada(identificador, nombre, visible, "administrador", True), codigos
