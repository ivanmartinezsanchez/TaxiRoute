"""Interfaz de terminal para gestionar carreras de TaxiRoute."""
from pathlib import Path

from prompt_toolkit import PromptSession

from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas
from taxiroute.historial import HistorialCSV
from taxiroute.registro import RegistroActividad
from taxiroute.servicio import ServicioCarreras, AYUDA, describir_error


class GestorTerminal(ServicioCarreras):
    """Adaptador compatible con los comandos existentes de la terminal."""


def main() -> None:
    ruta_tarifas = Path(__file__).resolve().parent.parent / "configuracion" / "tarifas.json"
    registro = RegistroActividad(Path.home() / ".taxiroute" / "actividad.jsonl")
    gestor = GestorTerminal(
        crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(ruta_tarifas)),
        historial=HistorialCSV(Path.home() / ".taxiroute" / "historial.csv"),
        registro=registro,
    )
    sesion = PromptSession(bottom_toolbar=gestor.estado_actual, refresh_interval=1)
    print("TaxiRoute — taxímetro digital")
    aviso = registro.registrar("aplicacion_iniciada")
    if aviso:
        print(aviso)
    print(AYUDA)
    while True:
        try:
            comando = sesion.prompt("TaxiRoute > ")
        except KeyboardInterrupt:
            aviso = registro.registrar("interrupcion_cancelada")
            print("\nInterrupción cancelada. Escribe 'salir' para terminar.")
            if aviso:
                print(aviso)
            continue
        except EOFError:
            aviso = registro.registrar("entrada_cerrada", carrera_activa=gestor.carrera is not None,
                                       carrera_pendiente=gestor.pendiente is not None)
            if aviso:
                print(aviso)
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
