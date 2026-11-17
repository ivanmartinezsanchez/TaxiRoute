"""Inicio explícito de una instalación LAN ya registrada.

No crea ni migra instalaciones. El acceso desde otros dispositivos y la
confianza del certificado deben configurarse por separado. El perfil de correo
se consulta únicamente con --conectar-gmail; no se envían correos aquí.
Las verificaciones no son atómicas frente a procesos Docker externos.
"""
import argparse
from pathlib import Path
import sys

from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, CargaProxyPosibleExposicion
from taxiroute.carga_red import cargar_correo_red
from taxiroute.cargar_gmail_windows import _estado_archivo
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto_red
from taxiroute.oauth_gmail import CLAVE_ALMACEN_ALTERNATIVA, SERVICIO_ALMACEN, _almacen_windows
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.reutilizacion_red import ErrorReutilizacionRed, reutilizar_red
from taxiroute.verificador_proxy_experimental import verificar_instalacion_red


def _proveedor_windows(identidad):
    """Consulta el perfil autorizado únicamente cuando se invoca el proveedor."""
    verificar_permisos_windows(identidad)
    perfil = _almacen_windows().get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN_ALTERNATIVA)
    if not isinstance(perfil, str) or not perfil:
        raise ValueError("No se encuentra el perfil de correo.")
    return perfil


def main(argv=None, *, proveedor=None):
    parser = argparse.ArgumentParser(description="Reutiliza una instalación de red ya registrada y experimental.")
    parser.add_argument("--identidad", required=True, type=Path, help="Carpeta de identidad de red ya preparada")
    parser.add_argument("--conectar-gmail", action="store_true", help="Solicitar la carga del perfil Gmail autorizado")
    args = parser.parse_args(argv)
    identidad = args.identidad
    correo_cargado = False
    try:
        print("TaxiRoute experimental: utiliza únicamente datos de prueba.", flush=True)
        evidencia = reutilizar_red(identidad)
        manifiesto = leer_manifiesto_red(identidad)
        if verificar_instalacion_red(identidad) != evidencia:
            raise ErrorReutilizacionRed("La instalación ha cambiado tras la comprobación.")
        if args.conectar_gmail:
            estado = _estado_archivo(evidencia.backend.identificador)
            if estado == "ausente":
                obtener = proveedor if proveedor is not None else lambda: _proveedor_windows(identidad)
                cargar_correo_red(identidad=identidad, evidencia_esperada=evidencia, proveedor=obtener)
                correo_cargado = True
                mensaje_correo = "Perfil de Gmail cargado en la instalación verificada."
            elif estado == "presente":
                mensaje_correo = "Ya existe un archivo temporal de correo; no se ha comprobado su identidad o vigencia."
            else:
                raise ValueError("Estado de correo desconocido.")
        else:
            mensaje_correo = "Correo administrativo no conectado por este arranque."
        if leer_manifiesto_red(identidad) != manifiesto or verificar_instalacion_red(identidad) != evidencia:
            raise ErrorReutilizacionRed("La instalación ha cambiado tras la comprobación.")
        print(mensaje_correo)
        print(f"Acceso de red: https://{manifiesto.direccion}:{manifiesto.puerto}/")
        print("El certificado debe ser confiable en cada dispositivo; puede aparecer una advertencia.")
        return 0
    except CargaProxyPosibleExposicion:
        print("No se pudo confirmar la carga de correo; pudo haberse transmitido. Revisa la instalación antes de reintentar.", file=sys.stderr)
    except (CargaProxyAntesEnvio, ErrorReutilizacionRed):
        if correo_cargado:
            print("Se cargó el correo, pero la verificación final falló. No reintentes sin revisar la instalación.", file=sys.stderr)
        else:
            print("No se pudo verificar el arranque o la carga de correo. Conserva la instalación para revisión.", file=sys.stderr)
    except Exception:
        if correo_cargado:
            print("Se cargó el correo, pero la verificación final falló. No reintentes sin revisar la instalación.", file=sys.stderr)
        else:
            print("No se pudo confirmar el estado de la instalación de red. Conserva sus datos para revisión.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
