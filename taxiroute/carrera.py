"""Reglas de cálculo de una carrera, sin depender de ninguna interfaz."""

from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from time import monotonic_ns
from typing import Callable


class EstadoVehiculo(str, Enum):
    """Estado que determina la tarifa aplicada durante el tramo."""

    PARADO = "parado"
    EN_MOVIMIENTO = "en movimiento"


class Carrera:
    """Una carrera activa desde su creación hasta su finalización.

    El reloj devuelve nanosegundos monotónicos. Es inyectable para probar
    las transiciones sin depender del paso real del tiempo.
    """

    TARIFAS = {
        EstadoVehiculo.PARADO: Decimal("0.02"),
        EstadoVehiculo.EN_MOVIMIENTO: Decimal("0.05"),
    }
    NANOSEGUNDOS_POR_SEGUNDO = Decimal("1000000000")
    CENTIMO = Decimal("0.01")

    def __init__(self, reloj: Callable[[], int] = monotonic_ns) -> None:
        self._reloj = reloj
        self._estado = EstadoVehiculo.PARADO
        self._inicio_tramo_ns = self._reloj()
        self._importe_acumulado = Decimal("0")
        self._finalizada = False
        self._total_final: Decimal | None = None

    @property
    def estado(self) -> EstadoVehiculo:
        return self._estado

    @property
    def finalizada(self) -> bool:
        return self._finalizada

    def _comprobar_activa(self) -> None:
        if self._finalizada:
            raise ValueError("La carrera ya ha finalizado.")

    def _importe_hasta(self, instante_ns: int) -> Decimal:
        duracion_ns = instante_ns - self._inicio_tramo_ns
        if duracion_ns < 0:
            raise ValueError("El reloj no puede retroceder durante una carrera.")
        segundos = Decimal(duracion_ns) / self.NANOSEGUNDOS_POR_SEGUNDO
        return self._importe_acumulado + segundos * self.TARIFAS[self._estado]

    def importe_actual(self) -> Decimal:
        """Devuelve el importe provisional redondeado, sin cerrar el tramo."""
        if self._finalizada:
            assert self._total_final is not None
            return self._total_final
        return self._importe_hasta(self._reloj()).quantize(
            self.CENTIMO, rounding=ROUND_HALF_UP
        )

    def cambiar_estado(self, nuevo_estado: EstadoVehiculo) -> None:
        """Cierra el tramo anterior y empieza otro con la tarifa nueva."""
        self._comprobar_activa()
        if not isinstance(nuevo_estado, EstadoVehiculo):
            raise ValueError("El estado debe ser parado o en movimiento.")
        if nuevo_estado == self._estado:
            return
        instante_ns = self._reloj()
        self._importe_acumulado = self._importe_hasta(instante_ns)
        self._inicio_tramo_ns = instante_ns
        self._estado = nuevo_estado

    def finalizar(self) -> Decimal:
        """Cierra la carrera y redondea el total una sola vez."""
        self._comprobar_activa()
        total = self._importe_hasta(self._reloj()).quantize(
            self.CENTIMO, rounding=ROUND_HALF_UP
        )
        self._total_final = total
        self._finalizada = True
        return total
