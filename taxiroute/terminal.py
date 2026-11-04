"""Interfaz de terminal para gestionar carreras de TaxiRoute."""

from collections.abc import Callable

from prompt_toolkit import PromptSession

from taxiroute.carrera import Carrera, EstadoVehiculo

AYUDA = """Comandos disponibles:
  iniciar    Iniciar una carrera (comienza en parado).
  mover      Indicar que el vehículo está en movimiento.
  parar      Indicar que el vehículo está parado.
  importe    Mostrar el estado y el importe actual.
  finalizar  Finalizar la carrera y mostrar el total.
  ayuda      Mostrar estos comandos.
  salir      Cerrar la aplicación cuando no haya carrera activa.
Escribe un comando y pulsa Intro."""


class GestorTerminal:
    """Traduce comandos a operaciones del dominio, sin depender de la pantalla."""

    def __init__(self, crear_carrera: Callable[[], Carrera] = Carrera) -> None:
        self._crear_carrera = crear_carrera
        self.carrera: Carrera | None = None

    def estado_actual(self) -> str:
        if self.carrera is None:
            return "Sin carrera activa"
        return (
            f"Estado: {self.carrera.estado.value} | "
            f"Importe: {self.carrera.importe_actual():.2f} €"
        )

    def ejecutar(self, comando: str) -> tuple[str, bool]:
        """Devuelve el mensaje al conductor y si debe terminar el programa."""
        comando = comando.strip().lower()
        if comando == "ayuda":
            return AYUDA, False
        if comando == "salir":
            if self.carrera is not None:
                return "Hay una carrera activa. Finalízala antes de salir.", False
            return "TaxiRoute cerrado.", True
        if comando == "iniciar":
            if self.carrera is not None:
                return "Ya hay una carrera activa. Finalízala antes de iniciar otra.", False
            self.carrera = self._crear_carrera()
            return "Carrera iniciada. Estado: parado.", False
        if comando not in {"mover", "parar", "importe", "finalizar"}:
            return "Comando no reconocido. Escribe 'ayuda' para ver las opciones.", False
        if self.carrera is None:
            return "No hay ninguna carrera activa. Escribe 'iniciar' para comenzar.", False
        if comando == "importe":
            return self.estado_actual(), False
        if comando == "finalizar":
            total = self.carrera.finalizar()
            self.carrera = None
            return f"Carrera finalizada. Total: {total:.2f} €", False
        estado = (
            EstadoVehiculo.EN_MOVIMIENTO if comando == "mover" else EstadoVehiculo.PARADO
        )
        if self.carrera.estado == estado:
            return f"El vehículo ya está en estado: {estado.value}.", False
        self.carrera.cambiar_estado(estado)
        return f"Estado actualizado: {estado.value}.", False


def main() -> None:
    gestor = GestorTerminal()
    sesion = PromptSession(bottom_toolbar=gestor.estado_actual, refresh_interval=1)
    print("TaxiRoute — taxímetro digital")
    print(AYUDA)
    while True:
        try:
            comando = sesion.prompt("TaxiRoute > ")
        except KeyboardInterrupt:
            print("\nInterrupción cancelada. Escribe 'salir' para terminar.")
            continue
        except EOFError:
            if gestor.carrera is not None:
                print("\nEntrada cerrada. La carrera activa se interrumpe sin guardar.")
            else:
                print("\nTaxiRoute cerrado.")
            break
        mensaje, salir = gestor.ejecutar(comando)
        print(mensaje)
        if salir:
            break


if __name__ == "__main__":
    main()
