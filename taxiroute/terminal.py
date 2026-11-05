"""Interfaz de terminal para gestionar carreras de TaxiRoute."""

import errno
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from prompt_toolkit import PromptSession

from taxiroute.carrera import Carrera, EstadoVehiculo
from taxiroute.historial import HistorialCSV, exportar_copia
from taxiroute.bloqueo import HistorialOcupado

AYUDA = """Comandos disponibles:
  iniciar    Iniciar una carrera (comienza en parado).
  mover      Indicar que el vehículo está en movimiento.
  parar      Indicar que el vehículo está parado.
  importe    Mostrar el estado y el importe actual.
  finalizar  Finalizar la carrera y mostrar el total.
  historial      Consultar todas las carreras guardadas.
  historial hoy  Consultar las carreras finalizadas hoy.
  reintentar Reintentar el guardado pendiente.
  exportar RUTA  Guardar una copia de la carrera pendiente.
  ayuda      Mostrar estos comandos.
  salir      Cerrar la aplicación cuando no haya carrera activa.
Escribe un comando y pulsa Intro."""


class GestorTerminal:
    """Traduce comandos a operaciones del dominio, sin depender de la pantalla."""

    def __init__(self, crear_carrera: Callable[[], Carrera] = Carrera, historial=None) -> None:
        self._crear_carrera = crear_carrera
        self.historial = historial
        self.pendiente = None
        self.carrera: Carrera | None = None

    def estado_actual(self) -> str:
        if self.pendiente is not None:
            return "Carrera finalizada pendiente de guardar"
        if self.carrera is None:
            return "Sin carrera activa"
        return (
            f"Estado: {self.carrera.estado.value} | "
            f"Importe: {self.carrera.importe_actual():.2f} €"
        )

    def ejecutar(self, comando: str) -> tuple[str, bool]:
        """Devuelve el mensaje al conductor y si debe terminar el programa."""
        entrada = comando.strip()
        partes = entrada.split(maxsplit=1)
        comando = partes[0].lower() if partes else ""
        if comando == "exportar":
            if self.pendiente is None:
                return "No hay ninguna carrera pendiente de guardar.", False
            if len(partes) < 2:
                return "Indica una ruta nueva: exportar C:\\ruta\\copia.csv", False
            ruta = partes[1].strip().strip('"')
            try:
                destino = exportar_copia(self.pendiente, ruta)
            except (OSError, ValueError) as error:
                return f"No se pudo crear la copia: {describir_error(error)} La carrera sigue pendiente.", False
            return f"Copia creada en {destino}. La carrera sigue pendiente en el historial principal; usa reintentar.", False
        consultar_hoy = comando == "historial" and len(partes) == 2 and partes[1].lower() == "hoy"
        if len(partes) > 1 and not consultar_hoy:
            return "Comando no reconocido. Escribe 'ayuda' para ver las opciones.", False
        if comando == "ayuda":
            return AYUDA, False
        if comando == "historial":
            if self.historial is None:
                return "El historial no está configurado.", False
            try:
                registros = self.historial.consultar()
            except (OSError, ValueError) as error:
                return f"No se puede consultar el historial: {error}", False
            if consultar_hoy:
                hoy = fecha_local_actual()
                try:
                    registros = [fila for fila in registros if fecha_final_local(fila) == hoy]
                except ValueError as error:
                    return f"No se puede filtrar el historial: {error}", False
                if not registros:
                    return "No hay carreras finalizadas hoy.", False
            elif not registros:
                return "No hay carreras guardadas.", False
            lineas = ["Carreras finalizadas hoy:" if consultar_hoy else "Carreras guardadas:"]
            for fila in registros:
                lineas.append(f"{fila['inicio']} | {fila['duracion_segundos']} s | {fila['importe']} € | {fila['identificador']}")
            return "\n".join(lineas), False
        if comando == "reintentar":
            if self.pendiente is None:
                return "No hay ninguna carrera pendiente de guardar.", False
            return self._guardar_pendiente(), False
        if self.pendiente is not None and comando in {"iniciar", "salir"}:
            return "Hay una carrera pendiente de guardar. Escribe 'reintentar'; si cierras la terminal podrías perderla.", False
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
            self.pendiente = self.carrera.resumen if self.historial is not None else None
            self.carrera = None
            mensaje = f"Carrera finalizada. Total: {total:.2f} €"
            if self.pendiente is not None:
                mensaje += "\n" + self._guardar_pendiente()
            return mensaje, False
        estado = (
            EstadoVehiculo.EN_MOVIMIENTO if comando == "mover" else EstadoVehiculo.PARADO
        )
        if self.carrera.estado == estado:
            return f"El vehículo ya está en estado: {estado.value}.", False
        self.carrera.cambiar_estado(estado)
        return f"Estado actualizado: {estado.value}.", False

    def _guardar_pendiente(self) -> str:
        try:
            self.historial.guardar(self.pendiente)
        except (OSError, ValueError) as error:
            return (f"No se ha podido guardar la carrera: {describir_error(error)} "
                    "Escribe 'reintentar' o 'exportar RUTA' para crear una copia en otro lugar. "
                    "Si cierras la terminal, podrías perder los datos pendientes.")
        self.pendiente = None
        return "Carrera guardada en el historial."


def fecha_local_actual() -> date:
    """Día actual según la zona horaria configurada en el ordenador."""
    return datetime.now().astimezone().date()


def a_fecha_local(instante: datetime) -> date:
    """Convierte cada instante usando la zona local aplicable a esa fecha."""
    return instante.astimezone().date()


def fecha_final_local(fila: dict) -> date:
    """Extrae el día local de finalización de un registro del historial."""
    try:
        instante = datetime.fromisoformat(fila["fin"])
        if instante.utcoffset() is None:
            raise ValueError("La fecha no indica su zona horaria")
        return a_fecha_local(instante)
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("hay una fecha de finalización inválida o sin zona horaria; revisa el archivo") from error


def describir_error(error: Exception) -> str:
    if isinstance(error, HistorialOcupado):
        return "El historial está en uso por otra ejecución. Espera y vuelve a intentarlo."
    if isinstance(error, FileExistsError):
        return "Ya existe un archivo en esa ruta; elige otro nombre."
    if isinstance(error, FileNotFoundError):
        return "No se encuentra la carpeta indicada; comprueba la ruta."
    if isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, "winerror", None) in (39, 112)):
        return "No queda espacio en la unidad. Libera espacio o usa otra unidad."
    if isinstance(error, OSError) and getattr(error, "winerror", None) in (32, 33):
        return "El archivo está en uso. Ciérralo en otros programas y vuelve a intentarlo."
    if isinstance(error, PermissionError):
        return "No hay permiso para acceder a la ruta. Revisa los permisos."
    if isinstance(error, ValueError):
        return f"{error}. Conserva el archivo y revísalo antes de continuar."
    return f"No se ha podido determinar la causa con seguridad ({error})."


def main() -> None:
    gestor = GestorTerminal(historial=HistorialCSV(Path.home() / ".taxiroute" / "historial.csv"))
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
            if gestor.pendiente is not None:
                print("\nEntrada cerrada. La carrera pendiente podría perderse sin guardar.")
            elif gestor.carrera is not None:
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
