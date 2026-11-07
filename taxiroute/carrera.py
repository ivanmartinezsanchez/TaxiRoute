"""Reglas de cálculo de una carrera, sin depender de ninguna interfaz."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from time import monotonic_ns
from typing import Callable
from uuid import uuid4


class EstadoVehiculo(str, Enum):
    """Estado que determina la tarifa aplicada durante el tramo."""

    PARADO = "parado"
    EN_MOVIMIENTO = "en movimiento"


@dataclass(frozen=True)
class ResumenCarrera:
    """Datos de una carrera finalizada, independientes del almacenamiento."""

    identificador: str
    inicio: datetime
    fin: datetime
    segundos_parado: Decimal
    segundos_movimiento: Decimal
    tarifa_parado: Decimal
    tarifa_movimiento: Decimal
    importe: Decimal

    @property
    def duracion_segundos(self) -> Decimal:
        return self.segundos_parado + self.segundos_movimiento


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

    def __init__(
        self,
        reloj: Callable[[], int] = monotonic_ns,
        fecha_actual: Callable[[], datetime] | None = None,
        tarifas: dict[EstadoVehiculo, Decimal] | None = None,
    ) -> None:
        self._reloj = reloj
        self._fecha_actual = fecha_actual or (lambda: datetime.now(timezone.utc))
        self._identificador = str(uuid4())
        self._inicio = self._fecha_actual()
        self._tarifas = dict(self.TARIFAS if tarifas is None else tarifas)
        self._tiempos_ns = {estado: 0 for estado in EstadoVehiculo}
        self._resumen: ResumenCarrera | None = None
        self._estado = EstadoVehiculo.PARADO
        self._inicio_tramo_ns = self._reloj()
        self._importe_acumulado = Decimal("0")
        self._finalizada = False
        self._total_final: Decimal | None = None

    @property
    def estado(self) -> EstadoVehiculo:
        return self._estado

    @property
    def tarifas(self) -> dict[EstadoVehiculo, Decimal]:
        """Copia de las tarifas fijadas al inicio; no permite modificar la carrera."""
        return dict(self._tarifas)

    @property
    def finalizada(self) -> bool:
        return self._finalizada

    @property
    def resumen(self) -> ResumenCarrera | None:
        """Devuelve los datos definitivos después de finalizar la carrera."""
        return self._resumen

    def _comprobar_activa(self) -> None:
        if self._finalizada:
            raise ValueError("La carrera ya ha finalizado.")

    def _importe_hasta(self, instante_ns: int) -> Decimal:
        duracion_ns = instante_ns - self._inicio_tramo_ns
        if duracion_ns < 0:
            raise ValueError("El reloj no puede retroceder durante una carrera.")
        segundos = Decimal(duracion_ns) / self.NANOSEGUNDOS_POR_SEGUNDO
        return self._importe_acumulado + segundos * self._tarifas[self._estado]

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
        self._tiempos_ns[self._estado] += instante_ns - self._inicio_tramo_ns
        self._inicio_tramo_ns = instante_ns
        self._estado = nuevo_estado

    def finalizar(self) -> Decimal:
        """Cierra la carrera y redondea el total una sola vez."""
        self._comprobar_activa()
        instante_ns = self._reloj()
        total = self._importe_hasta(instante_ns).quantize(
            self.CENTIMO, rounding=ROUND_HALF_UP
        )
        fin = self._fecha_actual()
        tiempos = self._tiempos_ns.copy()
        tiempos[self._estado] += instante_ns - self._inicio_tramo_ns
        self._resumen = ResumenCarrera(
            identificador=self._identificador,
            inicio=self._inicio,
            fin=fin,
            segundos_parado=Decimal(tiempos[EstadoVehiculo.PARADO]) / self.NANOSEGUNDOS_POR_SEGUNDO,
            segundos_movimiento=Decimal(tiempos[EstadoVehiculo.EN_MOVIMIENTO]) / self.NANOSEGUNDOS_POR_SEGUNDO,
            tarifa_parado=self._tarifas[EstadoVehiculo.PARADO],
            tarifa_movimiento=self._tarifas[EstadoVehiculo.EN_MOVIMIENTO],
            importe=total,
        )
        self._total_final = total
        self._finalizada = True
        return total
