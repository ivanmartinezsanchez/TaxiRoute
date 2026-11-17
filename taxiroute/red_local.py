"""Comprobaciones de la dirección local antes de configurar TaxiRoute en red."""
import socket

from taxiroute.servidor_sqlite_local import _validar_direccion


class DireccionRedNoDisponible(ValueError):
    """La dirección de red no se puede utilizar en este equipo."""


def comprobar_direccion_local(direccion):
    """Exige IPv4 privada y comprueba que el sistema permite asociarla.

    No publica ningún puerto ni confirma cortafuegos, conectividad de clientes,
    estabilidad DHCP o confianza de certificados. Se vuelve a comprobar al usarla.
    """
    try:
        _validar_direccion(direccion)
        if direccion == "127.0.0.1":
            raise ValueError("Se necesita una dirección de red privada.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as prueba:
            prueba.bind((direccion, 0))
    except (OSError, TypeError, ValueError) as exc:
        raise DireccionRedNoDisponible(
            "La dirección no es una IPv4 privada disponible en este equipo."
        ) from exc
    return direccion
