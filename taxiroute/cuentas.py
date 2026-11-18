"""Alta interna de cuentas; aún no expuesta por la web ni usada como autenticación."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import unicodedata
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.low_level import Type

from taxiroute.base_cuentas import conectar


# Perfil explícito: evita cambios silenciosos al actualizar la biblioteca.
_PROTECTOR = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16, type=Type.ID,
)


class DatosCuentaInvalidos(ValueError):
    """Datos que no cumplen la política de cuentas."""


class UsuarioDuplicado(ValueError):
    """El nombre normalizado ya está registrado."""


@dataclass(frozen=True)
class CuentaCreada:
    """Resultado público del alta: no incluye contraseña ni hash."""
    id: str
    nombre_usuario: str
    nombre_visible: str
    rol: str
    cambio_obligatorio: bool


def normalizar_usuario(nombre: str) -> tuple[str, str]:
    """Conserva la grafía normalizada y genera la clave de comparación."""
    if not isinstance(nombre, str):
        raise DatosCuentaInvalidos("El nombre de usuario debe ser texto.")
    visible = unicodedata.normalize("NFC", nombre)
    if not 3 <= len(visible) <= 40 or not all(
        c.isalnum() or c in "._-" for c in visible
    ):
        raise DatosCuentaInvalidos(
            "El usuario debe tener entre 3 y 40 caracteres: letras, números, punto, guion o guion bajo."
        )
    return visible, unicodedata.normalize("NFC", visible.casefold())


def validar_contrasena(contrasena: str) -> None:
    """Valida sin modificar la contraseña ni incluirla en los errores."""
    if not isinstance(contrasena, str):
        raise DatosCuentaInvalidos("La contraseña debe ser texto.")
    if not 12 <= len(contrasena) <= 128:
        raise DatosCuentaInvalidos("La contraseña debe tener entre 12 y 128 caracteres.")
    if any(unicodedata.category(c).startswith("C") for c in contrasena):
        raise DatosCuentaInvalidos("La contraseña no admite caracteres de control o invisibles de formato.")
    if not (
        sum(c.isupper() for c in contrasena) >= 1
        and sum(c.islower() for c in contrasena) >= 3
        and sum(c in "0123456789" for c in contrasena) >= 3
        and any(unicodedata.category(c)[0] in "PS" for c in contrasena)
    ):
        raise DatosCuentaInvalidos(
            "La contraseña necesita una mayúscula, tres minúsculas, tres números y un símbolo."
        )


def crear_cuenta(
    ruta: str | Path,
    *,
    nombre_usuario: str,
    nombre_visible: str,
    contrasena: str,
    rol: str = "conductor",
) -> CuentaCreada:
    """Inserta una cuenta en una base ya inicializada.

    Operación interna, no un endpoint ni una comprobación de permisos.
    La futura capa administrativa debe autorizar el alta y fijar el rol.
    Todas las cuentas nuevas requieren cambio de contraseña.
    """
    nombre, normalizado = normalizar_usuario(nombre_usuario)
    if rol not in ("administrador", "conductor"):
        raise DatosCuentaInvalidos("El rol debe ser administrador o conductor.")
    if not isinstance(nombre_visible, str):
        raise DatosCuentaInvalidos("El nombre visible debe ser texto.")
    visible = unicodedata.normalize("NFC", nombre_visible).strip()
    if not 1 <= len(visible) <= 100 or any(
        unicodedata.category(c).startswith("C") for c in visible
    ):
        raise DatosCuentaInvalidos("El nombre visible debe tener entre 1 y 100 caracteres sin controles.")
    validar_contrasena(contrasena)
    # El cálculo costoso se realiza antes de adquirir el bloqueo de escritura.
    protegido = _PROTECTOR.hash(contrasena)
    identificador = str(uuid4())
    fecha = datetime.now(timezone.utc).isoformat()
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            conexion.execute(
                """INSERT INTO usuarios
                   (id, nombre_usuario, nombre_usuario_normalizado, nombre_visible,
                    rol, hash_contrasena, creado_en, actualizado_en)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (identificador, nombre, normalizado, visible, rol, protegido, fecha, fecha),
            )
            conexion.commit()
        except sqlite3.IntegrityError as error:
            conexion.rollback()
            if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise UsuarioDuplicado("Ese nombre de usuario ya existe.") from None
            raise
        except BaseException:
            conexion.rollback()
            raise
    return CuentaCreada(identificador, nombre, visible, rol, True)
