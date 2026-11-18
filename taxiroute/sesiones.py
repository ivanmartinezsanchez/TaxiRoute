"""Sesiones internas persistentes; la protección HTTP pertenece a la futura interfaz."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from secrets import token_urlsafe
from uuid import uuid4

from taxiroute.acceso import _ahora, _disponible, comprobar_credenciales, PasoAcceso
from taxiroute.base_cuentas import conectar


INACTIVIDAD = timedelta(hours=1)
DURACION_MAXIMA = timedelta(hours=8)


@dataclass(frozen=True)
class IdentidadSesion:
    usuario_id: str
    nombre_visible: str
    rol: str


@dataclass(frozen=True)
class SesionEmitida:
    identidad: IdentidadSesion
    token: str = field(repr=False)


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


def _hash_token(token):
    if not isinstance(token, str) or not 40 <= len(token) <= 128:
        return None
    try:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
    except UnicodeError:
        return None


def _identidad_vigente(conexion, sesion, ahora):
    """Aplica las mismas reglas a consultas y operaciones autorizadas.

    No modifica la sesión: quien llama decide si registrar su revocación.
    Debe ejecutarse en la transacción que protege la operación.
    """
    if sesion is None or sesion["revocada_en"] is not None:
        return None
    usuario = conexion.execute(
        """SELECT u.*, c.correo_verificacion FROM usuarios u
           LEFT JOIN configuracion_administrador c ON c.usuario_id=u.id WHERE u.id=?""",
        (sesion["usuario_id"],),
    ).fetchone()
    ultima = datetime.fromisoformat(sesion["ultima_interaccion_en"])
    valida = (
        usuario is not None and usuario["habilitado"]
        and usuario["bloqueado_en"] is None and usuario["intentos_fallidos"] < 15
        and not usuario["cambio_obligatorio"]
        and usuario["version_credenciales"] == sesion["version_credenciales"]
        and usuario["rol"] == sesion["rol"]
        and (usuario["rol"] != "administrador" or (
            usuario["correo_verificacion"] is not None
            and usuario["correo_verificacion"] == sesion["correo_verificacion"]
        ))
        and ahora >= ultima
        and ahora < datetime.fromisoformat(sesion["caduca_en"])
        and ahora - ultima < INACTIVIDAD
    )
    if not valida:
        return None
    return IdentidadSesion(usuario["id"], usuario["nombre_visible"], usuario["rol"])


def _crear_en_transaccion(conexion, usuario, ahora):
    """Solo para flujos internos que completaron todos los factores en esta transacción."""
    if usuario["cambio_obligatorio"] or not _disponible(usuario, ahora):
        raise ValueError("El acceso no está completo.")
    token = token_urlsafe(32)
    correo = usuario["correo_verificacion"] if usuario["rol"] == "administrador" else None
    conexion.execute(
        """INSERT INTO sesiones
           (id, hash_token, usuario_id, version_credenciales, rol, correo_verificacion,
            creado_en, ultima_interaccion_en, caduca_en)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid4()), _hash_token(token), usuario["id"], usuario["version_credenciales"],
         usuario["rol"], correo, ahora.isoformat(), ahora.isoformat(),
         (ahora + DURACION_MAXIMA).isoformat()),
    )
    conexion.execute(
        """UPDATE usuarios SET intentos_fallidos=0, proximo_intento_en=NULL,
           ultimo_acceso_en=?, actualizado_en=? WHERE id=?""",
        (ahora.isoformat(), ahora.isoformat(), usuario["id"]),
    )
    return SesionEmitida(
        IdentidadSesion(usuario["id"], usuario["nombre_visible"], usuario["rol"]), token,
    )


class SesionNoAutorizada(Exception):
    """La sesión ya no está vigente o no tiene el rol requerido."""


class ServicioSesiones:
    def __init__(self, ruta, *, reloj=_ahora):
        self.ruta = ruta
        self._reloj = reloj

    def iniciar_conductor(self, nombre_usuario, contrasena):
        """No admite administradores ni contraseñas temporales."""
        resultado = comprobar_credenciales(
            self.ruta, nombre_usuario, contrasena, reloj=self._reloj,
        )
        if resultado.paso != PasoAcceso.CONTRASENA_VERIFICADA:
            return None
        with _transaccion(self.ruta) as conexion:
            usuario = conexion.execute(
                "SELECT * FROM usuarios WHERE id=?", (resultado.usuario_id,),
            ).fetchone()
            ahora = self._reloj()
            if (
                usuario is None or usuario["rol"] != "conductor"
                or usuario["version_credenciales"] != resultado.version_credenciales
                or usuario["cambio_obligatorio"] or not _disponible(usuario, ahora)
            ):
                return None
            return _crear_en_transaccion(conexion, usuario, ahora)

    def consultar(self, token, *, interaccion=False):
        """La interfaz del servidor decide interaccion; no aceptar un campo del cliente.

        Consultas automáticas deben usar el valor predeterminado False.
        Devuelve identidad vigente o None, sin modificar carreras.
        """
        protegido = _hash_token(token)
        if protegido is None:
            return None
        with _transaccion(self.ruta) as conexion:
            sesion = conexion.execute(
                "SELECT * FROM sesiones WHERE hash_token=?", (protegido,),
            ).fetchone()
            if sesion is None or sesion["revocada_en"] is not None:
                return None
            ahora = self._reloj()
            identidad = _identidad_vigente(conexion, sesion, ahora)
            if identidad is None:
                conexion.execute(
                    "UPDATE sesiones SET revocada_en=? WHERE id=?",
                    (ahora.isoformat(), sesion["id"]),
                )
                return None
            if interaccion:
                conexion.execute(
                    "UPDATE sesiones SET ultima_interaccion_en=? WHERE id=?",
                    (ahora.isoformat(), sesion["id"]),
                )
            return identidad

    def ejecutar_con_sesion_y_conexion(self, token, *, roles, operacion, interaccion=False):
        """Prototipo interno: valida y llama con identidad y conexión compartida.

        La operación debe ser breve, usar la conexión recibida y no confirmar,
        revertir o abrir otra escritura en esta base. La transacción no incluye
        cambios en memoria ni archivos.
        """
        if type(interaccion) is not bool:
            raise ValueError("La interacción debe ser booleana.")
        permitidos = frozenset(roles)
        if not permitidos or not permitidos <= {"conductor", "administrador"}:
            raise ValueError("Indica roles válidos.")
        protegido = _hash_token(token)
        if protegido is None:
            raise SesionNoAutorizada()
        with _transaccion(self.ruta) as conexion:
            sesion = conexion.execute(
                "SELECT * FROM sesiones WHERE hash_token=?", (protegido,)
            ).fetchone()
            ahora = self._reloj()
            identidad = _identidad_vigente(conexion, sesion, ahora)
            if identidad is None or identidad.rol not in permitidos:
                raise SesionNoAutorizada()
            resultado = operacion(identidad, conexion)
            if interaccion:
                conexion.execute("UPDATE sesiones SET ultima_interaccion_en=? WHERE id=?",
                                 (ahora.isoformat(), sesion["id"]))
            return resultado

    def ejecutar_con_sesion_vigente(self, token, *, roles, operacion, interaccion=False):
        """Prototipo: ordena una operación en memoria frente a cambios de sesión."""
        return self.ejecutar_con_sesion_y_conexion(
            token, roles=roles, interaccion=interaccion,
            operacion=lambda identidad, conexion: operacion(identidad))
    def cerrar(self, token):
        """Revoca únicamente la sesión identificada; no detiene el proceso ni carreras."""
        protegido = _hash_token(token)
        if protegido is None:
            return
        with _transaccion(self.ruta) as conexion:
            conexion.execute(
                "UPDATE sesiones SET revocada_en=? WHERE hash_token=? AND revocada_en IS NULL",
                (self._reloj().isoformat(), protegido),
            )
