"""Comprobación interna de credenciales. No emite sesiones ni concede permisos."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from secrets import token_urlsafe

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import DatosCuentaInvalidos, _PROTECTOR, normalizar_usuario, validar_contrasena


class PasoAcceso(str, Enum):
    DENEGADO = "denegado"
    PIN_PENDIENTE = "pin_pendiente"
    CAMBIO_PENDIENTE = "cambio_pendiente"
    CONTRASENA_VERIFICADA = "contrasena_verificada"


@dataclass(frozen=True)
class ResultadoCredenciales:
    """Resultado interno; no es un token y nunca autoriza una petición web."""
    paso: PasoAcceso
    usuario_id: str | None = None
    version_credenciales: int | None = None


_DENEGADO = ResultadoCredenciales(PasoAcceso.DENEGADO)


@lru_cache(maxsize=1)
def _hash_ficticio():
    # Para nombres inexistentes se realiza también una verificación costosa.
    return _PROTECTOR.hash(token_urlsafe(32))


def _ahora():
    return datetime.now(timezone.utc)


def _disponible(fila, ahora):
    if not fila["habilitado"] or fila["bloqueado_en"] is not None:
        return False
    if fila["intentos_fallidos"] >= 15:
        return False
    espera = fila["proximo_intento_en"]
    return espera is None or datetime.fromisoformat(espera) <= ahora


def comprobar_credenciales(ruta, nombre_usuario, contrasena, *, reloj=_ahora):
    """Verifica una contraseña y registra fallos de forma atómica.

    No reinicia contadores ni escribe ultimo_acceso_en: solo el futuro flujo
    completo de acceso podrá hacerlo. El reloj es inyectable para pruebas.
    """
    if not isinstance(contrasena, str) or len(contrasena) > 128:
        return _DENEGADO
    # Evita errores de codificación; no revalida composición al iniciar acceso.
    try:
        contrasena.encode("utf-8")
        _, normalizado = normalizar_usuario(nombre_usuario)
    except (DatosCuentaInvalidos, UnicodeError):
        return _DENEGADO
    with conectar(ruta) as conexion:
        fila = conexion.execute(
            "SELECT * FROM usuarios WHERE nombre_usuario_normalizado=?", (normalizado,)
        ).fetchone()
    inicio = reloj()
    disponible = fila is not None and _disponible(fila, inicio)
    protegido = fila["hash_contrasena"] if disponible else _hash_ficticio()
    try:
        correcta = _PROTECTOR.verify(protegido, contrasena)
    except VerifyMismatchError:
        correcta = False
    except (InvalidHashError, VerificationError):
        # Una credencial almacenada corrupta no permite acceso.
        return _DENEGADO
    if not disponible:
        return _DENEGADO

    # Argon2 se ejecuta fuera de la transacción de escritura.
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            actual = conexion.execute(
                "SELECT * FROM usuarios WHERE id=?", (fila["id"],)
            ).fetchone()
            if (
                actual is None or not actual["habilitado"]
                or actual["bloqueado_en"] is not None or actual["intentos_fallidos"] >= 15
                or actual["version_credenciales"] != fila["version_credenciales"]
                or actual["hash_contrasena"] != fila["hash_contrasena"]
                or actual["rol"] != fila["rol"]
            ):
                conexion.rollback()
                return _DENEGADO
            ahora = reloj()
            # Otro intento puede haber iniciado una espera mientras se calculaba Argon2.
            if not _disponible(actual, ahora):
                conexion.rollback()
                return _DENEGADO
            if not correcta:
                fallos = actual["intentos_fallidos"] + 1
                espera = min(60, 2 ** (fallos - 1))
                conexion.execute(
                    """UPDATE usuarios SET intentos_fallidos=?, bloqueado_en=?,
                       proximo_intento_en=?, actualizado_en=? WHERE id=?""",
                    (fallos, ahora.isoformat() if fallos >= 15 else None,
                     (ahora + timedelta(seconds=espera)).isoformat(),
                     ahora.isoformat(), actual["id"]),
                )
                conexion.commit()
                return _DENEGADO
            if actual["rol"] == "administrador":
                paso = PasoAcceso.PIN_PENDIENTE
            elif actual["cambio_obligatorio"]:
                paso = PasoAcceso.CAMBIO_PENDIENTE
            else:
                paso = PasoAcceso.CONTRASENA_VERIFICADA
            conexion.commit()
            return ResultadoCredenciales(paso, actual["id"], actual["version_credenciales"])
        except BaseException:
            conexion.rollback()
            raise


class CambioContrasenaDenegado(ValueError):
    """El cambio no puede completarse con el estado o credenciales actuales."""


def cambiar_contrasena(ruta, nombre_usuario, actual, nueva, *, reloj=_ahora):
    """Cambia la clave de un conductor comprobando de nuevo su estado.

    No crea sesión, no reinicia fallos y no desbloquea cuentas.
    Administradores: operación denegada hasta implementar su prueba de PIN.
    """
    resultado = comprobar_credenciales(ruta, nombre_usuario, actual, reloj=reloj)
    if resultado.paso not in (
        PasoAcceso.CAMBIO_PENDIENTE, PasoAcceso.CONTRASENA_VERIFICADA
    ):
        raise CambioContrasenaDenegado("No se puede realizar el cambio de contraseña.")
    validar_contrasena(nueva)
    if nueva == actual:
        raise DatosCuentaInvalidos("La contraseña nueva debe ser distinta de la actual.")
    protegido = _PROTECTOR.hash(nueva)
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            fila = conexion.execute(
                "SELECT * FROM usuarios WHERE id=?", (resultado.usuario_id,)
            ).fetchone()
            ahora = reloj()
            if (
                fila is None or fila["rol"] != "conductor"
                or fila["version_credenciales"] != resultado.version_credenciales
                or not _disponible(fila, ahora)
            ):
                raise CambioContrasenaDenegado(
                    "No se puede realizar el cambio de contraseña."
                )
            conexion.execute(
                """UPDATE usuarios SET hash_contrasena=?, cambio_obligatorio=0,
                   version_credenciales=version_credenciales+1, actualizado_en=?
                   WHERE id=?""",
                (protegido, ahora.isoformat(), fila["id"]),
            )
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise
