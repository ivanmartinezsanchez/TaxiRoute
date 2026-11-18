"""PIN administrativo con transporte inyectado; completa acceso sin correo predeterminado."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from secrets import randbelow, token_urlsafe

from taxiroute.acceso import comprobar_credenciales, PasoAcceso, _ahora, _disponible
from taxiroute.base_cuentas import conectar
from taxiroute.sesiones import _crear_en_transaccion, SesionEmitida


@contextmanager
def _transaccion(ruta):
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            yield conexion
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise


@dataclass(frozen=True)
class PinVerificado:
    """Resultado interno: nunca aceptar este objeto desde un cliente como autorización."""
    usuario_id: str
    version_credenciales: int
    cambio_obligatorio: bool
    autorizacion_cambio: str | None = field(default=None, repr=False)
    sesion: SesionEmitida | None = field(default=None, repr=False)


class ServicioPIN:
    """La clave y el transporte proceden de configuración privada del servidor.

    enviar(destinatario, codigo) debe terminar o lanzar una excepción.
    No hay transporte real predeterminado, impresión ni registro de códigos.
    """

    def __init__(self, ruta, *, clave_hmac, enviar, reloj=_ahora):
        if not isinstance(clave_hmac, bytes) or len(clave_hmac) < 32:
            raise ValueError("La clave HMAC debe contener al menos 32 bytes.")
        if not callable(enviar):
            raise TypeError("Es necesario proporcionar un transporte de correo.")
        self.ruta = ruta
        self._clave = clave_hmac
        self._enviar = enviar
        self._reloj = reloj

    def _verificador(self, desafio, codigo):
        contenido = json.dumps(
            [desafio["id"], desafio["usuario_id"], desafio["version_credenciales"],
             desafio["correo_destino"], codigo],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
        return hmac.digest(self._clave, contenido, "sha256").hex()

    @staticmethod
    def _usuario(conexion, usuario_id):
        return conexion.execute(
            """SELECT u.*, c.correo_verificacion FROM usuarios u
               JOIN configuracion_administrador c ON c.usuario_id=u.id WHERE u.id=?""",
            (usuario_id,),
        ).fetchone()

    def _vigente(self, desafio, usuario, ahora):
        return (
            usuario is not None and usuario["rol"] == "administrador"
            and _disponible(usuario, ahora)
            and usuario["version_credenciales"] == desafio["version_credenciales"]
            and usuario["correo_verificacion"] == desafio["correo_destino"]
            and desafio["invalidado_en"] is None and desafio["consumido_en"] is None
            and ahora < datetime.fromisoformat(desafio["caduca_en"])
        )

    def solicitar(self, nombre_usuario, contrasena):
        """Comprueba contraseña, reserva envío y devuelve ID opaco o None.

        Reenvíos: nueva comprobación de contraseña, un minuto de espera,
        máximo tres reservas por quince minutos, incluidas entregas fallidas.
        """
        resultado = comprobar_credenciales(
            self.ruta, nombre_usuario, contrasena, reloj=self._reloj,
        )
        if resultado.paso != PasoAcceso.PIN_PENDIENTE:
            return None
        with _transaccion(self.ruta) as conexion:
            ahora = self._reloj()
            usuario = self._usuario(conexion, resultado.usuario_id)
            if (
                usuario is None or usuario["rol"] != "administrador"
                or usuario["version_credenciales"] != resultado.version_credenciales
                or not _disponible(usuario, ahora)
            ):
                return None
            correo = usuario["correo_verificacion"]
            if "\r" in correo or "\n" in correo or "@" not in correo or len(correo) > 254:
                return None
            reservas = conexion.execute(
                "SELECT creado_en FROM desafios_acceso WHERE usuario_id=?",
                (usuario["id"],),
            ).fetchall()
            fechas = [datetime.fromisoformat(f["creado_en"]) for f in reservas]
            if any(f > ahora - timedelta(minutes=1) for f in fechas):
                return None
            if sum(f > ahora - timedelta(minutes=15) for f in fechas) >= 3:
                return None
            codigo = str(1_000_000 + randbelow(9_000_000))
            desafio = dict(
                id=token_urlsafe(32), usuario_id=usuario["id"],
                version_credenciales=usuario["version_credenciales"],
                correo_destino=correo,
            )
            conexion.execute(
                """UPDATE desafios_acceso SET invalidado_en=?
                   WHERE usuario_id=? AND consumido_en IS NULL AND invalidado_en IS NULL""",
                (ahora.isoformat(), usuario["id"]),
            )
            conexion.execute(
                """INSERT INTO desafios_acceso
                   (id, usuario_id, version_credenciales, correo_destino, verificador_pin,
                    creado_en, caduca_en, estado_envio)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente')""",
                (desafio["id"], usuario["id"], desafio["version_credenciales"], correo,
                 self._verificador(desafio, codigo), ahora.isoformat(),
                 (ahora + timedelta(minutes=5)).isoformat()),
            )
        # El transporte nunca mantiene una transacción SQLite abierta.
        try:
            self._enviar(correo, codigo)
        except Exception:
            with _transaccion(self.ruta) as conexion:
                conexion.execute(
                    """UPDATE desafios_acceso SET estado_envio='fallido', invalidado_en=?
                       WHERE id=? AND estado_envio='pendiente'""",
                    (self._reloj().isoformat(), desafio["id"]),
                )
            return None
        finally:
            # No garantiza borrado seguro de memoria; evita retenerlo en el servicio.
            del codigo
        with _transaccion(self.ruta) as conexion:
            guardado = conexion.execute(
                "SELECT * FROM desafios_acceso WHERE id=?", (desafio["id"],)
            ).fetchone()
            usuario = self._usuario(conexion, desafio["usuario_id"])
            if (
                guardado is None or guardado["estado_envio"] != "pendiente"
                or not self._vigente(guardado, usuario, self._reloj())
            ):
                return None
            conexion.execute(
                "UPDATE desafios_acceso SET estado_envio='enviado' WHERE id=?", (desafio["id"],)
            )
        return desafio["id"]

    def verificar(self, desafio_id, codigo):
        """Consume el PIN y emite autorización de cambio o sesión en la misma transacción."""
        if not isinstance(desafio_id, str) or len(desafio_id) > 128:
            return None
        with _transaccion(self.ruta) as conexion:
            desafio = conexion.execute(
                "SELECT * FROM desafios_acceso WHERE id=?", (desafio_id,)
            ).fetchone()
            if desafio is None or desafio["estado_envio"] != "enviado":
                return None
            ahora = self._reloj()
            usuario = self._usuario(conexion, desafio["usuario_id"])
            if not self._vigente(desafio, usuario, ahora):
                return None
            formato = (
                isinstance(codigo, str) and len(codigo) == 7
                and all(c in "0123456789" for c in codigo)
            )
            correcto = formato and hmac.compare_digest(
                self._verificador(desafio, codigo), desafio["verificador_pin"]
            )
            if not correcto:
                fallos = usuario["intentos_fallidos"] + 1
                conexion.execute(
                    """UPDATE usuarios SET intentos_fallidos=?, bloqueado_en=?,
                       proximo_intento_en=?, actualizado_en=? WHERE id=?""",
                    (fallos, ahora.isoformat() if fallos >= 15 else None,
                     (ahora + timedelta(seconds=min(60, 2 ** (fallos - 1)))).isoformat(),
                     ahora.isoformat(), usuario["id"]),
                )
                conexion.execute(
                    "UPDATE desafios_acceso SET fallos=fallos+1 WHERE id=?", (desafio_id,)
                )
                return None
            conexion.execute(
                "UPDATE desafios_acceso SET consumido_en=? WHERE id=?",
                (ahora.isoformat(), desafio_id),
            )
            autorizacion = None
            sesion = None
            if usuario["cambio_obligatorio"]:
                autorizacion = token_urlsafe(32)
                conexion.execute(
                    """INSERT INTO autorizaciones_cambio
                       (hash_token, desafio_id, creado_en, caduca_en) VALUES (?, ?, ?, ?)""",
                    (hashlib.sha256(autorizacion.encode("ascii")).hexdigest(), desafio_id,
                     ahora.isoformat(), (ahora + timedelta(minutes=5)).isoformat()),
                )
            else:
                sesion = _crear_en_transaccion(conexion, usuario, ahora)
            return PinVerificado(
                usuario["id"], usuario["version_credenciales"],
                bool(usuario["cambio_obligatorio"]), autorizacion, sesion,
            )
