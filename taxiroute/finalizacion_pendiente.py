"""Transición experimental de un punto guardado a resumen pendiente de CSV.

Funciones internas: el llamador debe comprobar la identidad y coordinar la
captura con la carrera viva. No finaliza carreras en memoria ni escribe CSV.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from uuid import uuid4

from taxiroute.base_cuentas import conectar
from taxiroute.carrera import ResumenCarrera
from taxiroute.puntos_control import (
    PuntoControl, PuntoControlInvalido, _desde_fila, _validar, _guardar_punto_en_transaccion,
)


@dataclass(frozen=True)
class ResumenPendiente:
    punto: PuntoControl
    fin: datetime
    importe: Decimal


def _importe(punto: PuntoControl) -> Decimal:
    """Suma ambos tramos sin redondeos intermedios y redondea a céntimos."""
    with localcontext() as contexto:
        contexto.prec = 80
        nanosegundos = Decimal(1_000_000_000)
        total = (Decimal(punto.parado_ns) * Decimal(punto.tarifa_parado)
                 + Decimal(punto.movimiento_ns) * Decimal(punto.tarifa_movimiento)) / nanosegundos
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def resumen_para_historial(pendiente: ResumenPendiente) -> ResumenCarrera:
    """Convierte un resumen pendiente validado al formato del historial CSV.

    No autentica, comprueba existencia en la base ni escribe en el historial.
    """
    if not isinstance(pendiente, ResumenPendiente):
        raise PuntoControlInvalido("Se necesita un resumen pendiente válido.")
    _, control = _validar(pendiente.punto)
    fin = pendiente.fin
    if not isinstance(fin, datetime) or fin.tzinfo is None or fin.utcoffset() is None:
        raise PuntoControlInvalido("La fecha final debe incluir zona horaria.")
    if fin.astimezone(timezone.utc) != control:
        raise PuntoControlInvalido("La fecha final no coincide con la captura.")
    if not isinstance(pendiente.importe, Decimal) or not pendiente.importe.is_finite():
        raise PuntoControlInvalido("El importe pendiente no es válido.")
    importe_canonico = _importe(pendiente.punto)
    if pendiente.importe != importe_canonico:
        raise PuntoControlInvalido("El importe no coincide con los tiempos y tarifas.")
    punto = pendiente.punto
    with localcontext() as contexto:
        contexto.prec = 80
        escala = Decimal(1_000_000_000)
        return ResumenCarrera(
            identificador=punto.carrera_id,
            inicio=punto.inicio.astimezone(timezone.utc),
            fin=fin.astimezone(timezone.utc),
            segundos_parado=Decimal(punto.parado_ns) / escala,
            segundos_movimiento=Decimal(punto.movimiento_ns) / escala,
            tarifa_parado=Decimal(punto.tarifa_parado),
            tarifa_movimiento=Decimal(punto.tarifa_movimiento),
            importe=importe_canonico,
        )

def _finalizar_en_transaccion(conexion, punto: PuntoControl, fin: datetime) -> ResumenPendiente:
    """Marca pendiente un punto ya guardado; la transacción la controla el llamador."""
    if not conexion.in_transaction:
        raise ValueError("Se necesita una transacción de escritura activa.")
    _, control = _validar(punto)
    if not isinstance(fin, datetime) or fin.tzinfo is None or fin.utcoffset() is None:
        raise PuntoControlInvalido("La fecha final debe incluir zona horaria.")
    fin_utc = fin.astimezone(timezone.utc)
    if fin_utc != control:
        raise PuntoControlInvalido("La finalización debe corresponder al instante de la captura.")
    importe = _importe(punto)
    fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=?",
                            (punto.carrera_id,)).fetchone()
    if fila is None or fila["usuario_id"] != punto.usuario_id:
        raise PuntoControlInvalido("No se encuentra la carrera activa correspondiente.")
    try:
        anterior = _desde_fila(fila)
    except (ValueError, TypeError, OverflowError) as error:
        raise PuntoControlInvalido("El punto guardado necesita revisión.") from error
    if anterior != punto:
        raise PuntoControlInvalido("La captura no coincide con el último punto guardado.")
    if fila["estado_registro"] == "guardado_pendiente":
        if fila["fin"] != fin_utc.isoformat() or fila["importe_final"] != str(importe):
            raise PuntoControlInvalido("El resumen pendiente presenta datos diferentes.")
    elif fila["estado_registro"] == "activa":
        resultado = conexion.execute("""UPDATE carreras_recuperacion
            SET estado_registro='guardado_pendiente', fin=?, importe_final=?
            WHERE id=? AND usuario_id=? AND estado_registro='activa'""",
            (fin_utc.isoformat(), str(importe), punto.carrera_id, punto.usuario_id))
        if resultado.rowcount != 1:
            raise PuntoControlInvalido("No se ha podido guardar el resumen pendiente.")
    else:
        raise PuntoControlInvalido("La carrera no admite esta finalización.")
    return ResumenPendiente(punto=punto, fin=fin_utc, importe=importe)


def finalizar_punto_guardado(ruta: str | Path, punto: PuntoControl, fin: datetime) -> ResumenPendiente:
    """Marca pendiente un punto idéntico ya guardado, sin escribir en CSV."""
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            resumen = _finalizar_en_transaccion(conexion, punto, fin)
            conexion.commit()
            return resumen
        except BaseException:
            conexion.rollback()
            raise


def preparar_punto_final_en_transaccion(conexion, punto: PuntoControl, fin: datetime) -> ResumenPendiente:
    """Guarda la captura final y la marca pendiente en la transacción del llamador.

    Requiere una transacción de escritura serializada ya abierta. El llamador
    debe autenticar, coordinar la carrera y confirmar o revertir la transacción.
    No modifica la carrera en memoria ni escribe el CSV.
    """
    if not conexion.in_transaction:
        raise ValueError("Se necesita una transacción de escritura activa.")
    # El nombre procede de UUID, nunca de entrada externa.
    marca = "preparar_" + uuid4().hex
    conexion.execute(f"SAVEPOINT {marca}")
    try:
        _validar(punto)
        fila = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=?",
                                (punto.carrera_id,)).fetchone()
        if fila is None:
            _guardar_punto_en_transaccion(conexion, punto)
        else:
            try:
                anterior = _desde_fila(fila)
            except (ValueError, TypeError, OverflowError) as error:
                raise PuntoControlInvalido("El punto guardado necesita revisión.") from error
            if anterior != punto:
                _guardar_punto_en_transaccion(conexion, punto)
        resumen = _finalizar_en_transaccion(conexion, punto, fin)
        conexion.execute(f"RELEASE SAVEPOINT {marca}")
        return resumen
    except BaseException:
        if conexion.in_transaction:
            conexion.execute(f"ROLLBACK TO SAVEPOINT {marca}")
            conexion.execute(f"RELEASE SAVEPOINT {marca}")
        raise


def preparar_punto_final(ruta: str | Path, punto: PuntoControl, fin: datetime) -> ResumenPendiente:
    """Ejecuta la preparación experimental en su propia transacción SQLite."""
    with conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            resumen = preparar_punto_final_en_transaccion(conexion, punto, fin)
            conexion.commit()
            return resumen
        except BaseException:
            conexion.rollback()
            raise

def preparar_punto_final_con_sesion(sesiones, token, punto: PuntoControl) -> ResumenPendiente:
    """Prototipo interno: usa una sesión y conexión compartida, sin tocar memoria.

    El llamador debe obtener el punto de una captura coordinada del servidor;
    nunca debe aceptar una captura enviada por el cliente. No habilitar en web
    hasta resolver coordinación con acciones y reconciliación tras fallos.
    """
    from taxiroute.sesiones import SesionNoAutorizada

    _validar(punto)
    def preparar(identidad, conexion):
        if identidad.usuario_id != punto.usuario_id:
            raise SesionNoAutorizada()
        return preparar_punto_final_en_transaccion(conexion, punto, punto.ultimo_control)
    return sesiones.ejecutar_con_sesion_y_conexion(
        token, roles={"conductor", "administrador"}, operacion=preparar)