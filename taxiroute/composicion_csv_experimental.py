"""Composición local experimental de fase 3 con un historial CSV por cuenta.

No migra historiales ni configura TLS. El proceso debe seguir activo durante
las carreras; esta variante no recupera una carrera activa tras un reinicio.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from uuid import UUID

from taxiroute.acceso import _ahora
from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial import HistorialCSV
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


def _historial_de_cuenta(carpeta, usuario):
    try:
        if not isinstance(usuario, str) or str(UUID(usuario)) != usuario:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Identificador de cuenta no válido para el historial.") from None
    directorio = carpeta / usuario
    archivo = directorio / "historial.csv"
    if directorio.is_symlink() or archivo.is_symlink() or (directorio / "historial.csv.lock").is_symlink():
        raise ValueError("El historial no puede usar enlaces simbólicos.")
    if directorio.exists() and not directorio.is_dir():
        raise ValueError("El directorio de historial no es válido.")
    return HistorialCSV(archivo)


@contextmanager
def abrir_app_csv_experimental(*, ruta_cuentas, carpeta_historial, ruta_tarifas,
                               clave_hmac, enviar_pin, reloj=_ahora):
    """Abre una web CSV con bloqueo cooperativo mientras dure el contexto.

    Exige archivos y carpeta existentes. Rechaza bases con registros durables
    para no separar una misma cuenta entre CSV y el historial SQLite de fase 4.
    No inicia servidor ni crea cuentas, y no está preparada para datos reales.
    """
    cuentas, tarifas, carpeta = Path(ruta_cuentas), Path(ruta_tarifas), Path(carpeta_historial)
    if (cuentas.is_symlink() or not cuentas.is_file() or tarifas.is_symlink() or not tarifas.is_file()
            or carpeta.is_symlink() or not carpeta.is_dir() or cuentas.resolve() == tarifas.resolve()):
        raise ValueError("Se necesitan cuentas, tarifas y carpeta de historial existentes y válidas.")
    cuentas, tarifas, carpeta = cuentas.resolve(), tarifas.resolve(), carpeta.resolve()
    for directorio in (cuentas.parent, carpeta):
        if any((padre / ".git").exists() or (padre / ".git").is_symlink()
               for padre in (directorio, *directorio.parents)):
            raise ValueError("Las cuentas y los historiales deben estar fuera de un repositorio Git.")
    if (cuentas.parent / "servidor.lock").is_symlink():
        raise ValueError("El bloqueo de la aplicación no puede ser un enlace.")
    with bloqueo_historial(cuentas.parent / "servidor", espera=0):
        cargar_tarifas(tarifas)
        sesiones = ServicioSesiones(cuentas, reloj=reloj)
        pin = ServicioPIN(cuentas, clave_hmac=clave_hmac, enviar=enviar_pin, reloj=reloj)

        def historial(usuario):
            return _historial_de_cuenta(carpeta, usuario)

        def servicio(usuario):
            return ServicioCarreras(crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(tarifas)),
                                    historial=historial(usuario))

        # Validar la identidad, el esquema y todos los registros pendientes.
        crear_flota_con_recuperacion(cuentas, servicio)
        with closing(sqlite3.connect(cuentas.as_uri() + "?mode=ro", uri=True, timeout=5)) as conexion:
            for tabla in ("carreras_recuperacion", "intenciones_operacion", "recibos_operacion"):
                if conexion.execute(f"SELECT 1 FROM {tabla} LIMIT 1").fetchone():
                    raise ValueError("La base contiene registros durables; no se puede abrir en modo CSV.")
        flota = FlotaEnMemoria(servicio)
        yield crear_app_integrada(sesiones, pin, flota, historial, ruta_tarifas=tarifas)
