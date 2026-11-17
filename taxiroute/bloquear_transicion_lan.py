"""Preparación conservadora de una transición desde la instalación local.

El marcador impide arrancar por el lanzador cooperativo existente. No detiene
servicios que ya estén activos ni protege frente a Docker o procesos externos.
La migración y el retorno a la instalación anterior necesitan revisión propia.
"""
from pathlib import Path
import os

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.copia_sqlite_experimental import _validar
from taxiroute.instalacion_inicial import revisar_instalacion
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.preparacion_actualizacion import instalacion_detenida
from taxiroute.recuperar_copia_experimental import _huella_estado
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION

_MARCADOR_COPIA = 'COPIA_DATOS_PREVIA.txt'
_CONTENIDO_COPIA = 'TaxiRoute: copia de datos previa comprobada; no es una restauración completa.\n'
_CONTENIDO_TRANSICION = b'TaxiRoute: transicion pendiente de revision.\n'


class TransicionRedError(RuntimeError):
    """La transición no puede prepararse sin revisión."""


def bloquear_local_para_transicion(carpeta, copia):
    """Comprueba la copia y bloquea el arranque local cooperativo.

    Ambas bases deben permanecer detenidas. Si falla la escritura del marcador
    se conserva lo escrito para revisión. No inicia ni activa ninguna LAN y no
    puede impedir arranques directos con Docker ni cambios no cooperativos.
    """
    try:
        carpeta, copia = Path(carpeta).expanduser().absolute(), Path(copia).expanduser().absolute()
        verificar_permisos_windows(copia)
        with instalacion_detenida(carpeta):
            rutas = revisar_instalacion(carpeta)
            origen = carpeta.resolve(strict=True)
            respaldo = copia.resolve(strict=True)
            if respaldo == origen or origen in respaldo.parents or respaldo in origen.parents:
                raise ValueError('La copia debe estar separada.')
            marcador_copia = copia / _MARCADOR_COPIA
            if (marcador_copia.is_symlink() or not marcador_copia.is_file()
                    or marcador_copia.read_text(encoding='utf-8') != _CONTENIDO_COPIA):
                raise ValueError('La copia no está completa.')
            with bloqueo_historial(copia / 'servidor', espera=0):
                verificar_permisos_windows(copia)
                _validar(copia)
                if _huella_estado(copia) != _huella_estado(rutas.datos):
                    raise ValueError('La copia no coincide.')
                if _docker(['ps', '--quiet', '--no-trunc']).strip():
                    raise ValueError('Hay contenedores activos.')
                verificar_permisos_windows(copia)
                if marcador_copia.read_text(encoding='utf-8') != _CONTENIDO_COPIA:
                    raise ValueError('La copia ha cambiado.')
                marcador = rutas.carpeta / 'proxy-local' / MARCADOR_ACTUALIZACION
                with marcador.open('xb') as salida:
                    salida.write(_CONTENIDO_TRANSICION)
                    salida.flush()
                    os.fsync(salida.fileno())
        return marcador
    except Exception:
        raise TransicionRedError('No se pudo preparar la transición; conserva los datos y revisa cualquier marcador.') from None
