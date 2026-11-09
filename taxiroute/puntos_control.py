"""Lectura y escritura experimental de puntos de control de carreras activas.

El llamador debe autenticar y autorizar al usuario. No ejecuta temporizadores,
recupera carreras ni coordina el historial CSV.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from taxiroute.base_cuentas import conectar
from taxiroute.carrera import CapturaCarrera
from taxiroute.configuracion import ConfiguracionError, validar_tarifas


class PuntoControlInvalido(ValueError):
    """El punto es inválido, antiguo o no corresponde a la carrera existente."""


@dataclass(frozen=True)
class PuntoControl:
    carrera_id: str
    usuario_id: str
    inicio: datetime
    ultimo_control: datetime
    estado_vehiculo: str
    parado_ns: int
    movimiento_ns: int
    tarifa_parado: str
    tarifa_movimiento: str
    revision: int


def _validar(punto):
    if not isinstance(punto, PuntoControl):
        raise PuntoControlInvalido("Se necesita un punto de control válido.")
    for texto in (punto.carrera_id, punto.usuario_id):
        if not isinstance(texto, str) or not texto.strip() or texto != texto.strip():
            raise PuntoControlInvalido("Identificador no válido.")
    for fecha in (punto.inicio, punto.ultimo_control):
        if not isinstance(fecha, datetime) or fecha.tzinfo is None or fecha.utcoffset() is None:
            raise PuntoControlInvalido("Las fechas deben incluir zona horaria.")
    inicio = punto.inicio.astimezone(timezone.utc)
    control = punto.ultimo_control.astimezone(timezone.utc)
    if control < inicio:
        raise PuntoControlInvalido("El control no puede ser anterior al inicio.")
    if punto.estado_vehiculo not in ("parado", "en movimiento"):
        raise PuntoControlInvalido("Estado de vehículo no válido.")
    for numero in (punto.parado_ns, punto.movimiento_ns, punto.revision):
        if type(numero) is not int or not 0 <= numero <= 2**63 - 1:
            raise PuntoControlInvalido("Los tiempos y la revisión deben ser enteros no negativos.")
    try:
        validar_tarifas({"parado": punto.tarifa_parado, "en_movimiento": punto.tarifa_movimiento})
    except ConfiguracionError as error:
        raise PuntoControlInvalido("Tarifas no válidas.") from error
    return inicio, control


def punto_de_captura(captura: CapturaCarrera, usuario_id: str, revision: int) -> PuntoControl:
    """Convierte una captura en un punto; la identidad debe estar autorizada."""
    if not isinstance(captura, CapturaCarrera):
        raise PuntoControlInvalido("Se necesita una captura de carrera.")
    punto = PuntoControl(
        carrera_id=captura.identificador, usuario_id=usuario_id,
        inicio=captura.inicio, ultimo_control=captura.instante,
        estado_vehiculo=captura.estado.value,
        parado_ns=captura.parado_ns, movimiento_ns=captura.movimiento_ns,
        tarifa_parado=format(captura.tarifa_parado, "f"),
        tarifa_movimiento=format(captura.tarifa_movimiento, "f"), revision=revision,
    )
    _validar(punto)
    return punto

def _guardar_punto_en_transaccion(conexion, punto: PuntoControl) -> None:
    """Requiere transacción de escritura serializada; no realiza commit."""
    if not conexion.in_transaction:
        raise ValueError("Se necesita una transacción activa.")
    inicio, control = _validar(punto)
    anterior = conexion.execute(
        "SELECT * FROM carreras_recuperacion WHERE id=?", (punto.carrera_id,)
    ).fetchone()
    if anterior is None:
        conexion.execute("""INSERT INTO carreras_recuperacion
            (id, usuario_id, estado_registro, estado_vehiculo, inicio, ultimo_control,
             parado_ns, movimiento_ns, tarifa_parado, tarifa_movimiento, revision)
            VALUES (?, ?, 'activa', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (punto.carrera_id, punto.usuario_id, punto.estado_vehiculo,
             inicio.isoformat(), control.isoformat(), punto.parado_ns, punto.movimiento_ns,
             punto.tarifa_parado, punto.tarifa_movimiento, punto.revision))
    else:
        try:
            previo = _desde_fila(anterior)
        except (ValueError, TypeError, OverflowError) as error:
            raise PuntoControlInvalido("El punto guardado necesita revisión.") from error
        if (anterior["estado_registro"] != "activa" or previo.usuario_id != punto.usuario_id
                or previo.inicio != inicio or previo.tarifa_parado != punto.tarifa_parado
                or previo.tarifa_movimiento != punto.tarifa_movimiento
                or control <= previo.ultimo_control or punto.revision < previo.revision
                or punto.parado_ns < previo.parado_ns or punto.movimiento_ns < previo.movimiento_ns
                or (punto.estado_vehiculo != previo.estado_vehiculo and punto.revision == previo.revision)):
            raise PuntoControlInvalido("El punto es antiguo o incompatible con la carrera guardada.")
        conexion.execute("""UPDATE carreras_recuperacion
            SET estado_vehiculo=?, ultimo_control=?, parado_ns=?, movimiento_ns=?, revision=?
            WHERE id=?""", (punto.estado_vehiculo, control.isoformat(), punto.parado_ns,
                            punto.movimiento_ns, punto.revision, punto.carrera_id))


def guardar_punto(ruta: str | Path, punto: PuntoControl) -> None:
    """Inserta o actualiza un punto activo; no confirma operaciones de la web."""
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            _guardar_punto_en_transaccion(conexion, punto)
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise

def _desde_fila(fila):
    punto = PuntoControl(
        carrera_id=fila["id"], usuario_id=fila["usuario_id"],
        inicio=datetime.fromisoformat(fila["inicio"]),
        ultimo_control=datetime.fromisoformat(fila["ultimo_control"]),
        estado_vehiculo=fila["estado_vehiculo"], parado_ns=fila["parado_ns"],
        movimiento_ns=fila["movimiento_ns"], tarifa_parado=fila["tarifa_parado"],
        tarifa_movimiento=fila["tarifa_movimiento"], revision=fila["revision"],
    )
    _validar(punto)
    return punto


def leer_punto(ruta: str | Path, usuario_id: str, carrera_id: str) -> PuntoControl | None:
    """Consulta un punto activo por ambos identificadores; no autentica al llamador."""
    if any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in (usuario_id, carrera_id)):
        raise PuntoControlInvalido("Identificador no válido.")
    with conectar(ruta) as conexion:
        fila = conexion.execute("""SELECT * FROM carreras_recuperacion
            WHERE id=? AND usuario_id=? AND estado_registro='activa'""",
            (carrera_id, usuario_id)).fetchone()
    if fila is None:
        return None
    try:
        return _desde_fila(fila)
    except (ValueError, TypeError, OverflowError) as error:
        raise PuntoControlInvalido("El punto guardado necesita revisión.") from error