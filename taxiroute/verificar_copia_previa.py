"""Verificación aislada de la copia previa de datos de TaxiRoute.

No restaura instalaciones ni valida certificados o secretos. Las comparaciones
solo son válidas durante la comprobación bajo bloqueos cooperativos.
"""
from pathlib import Path

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.copia_sqlite_experimental import _validar
from taxiroute.instalacion_inicial import revisar_instalacion
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.preparacion_actualizacion import instalacion_detenida
from taxiroute.recuperar_copia_experimental import _huella_estado

_MARCADOR = 'COPIA_DATOS_PREVIA.txt'
_CONTENIDO = 'TaxiRoute: copia de datos previa comprobada; no es una restauración completa.\n'


class VerificacionCopiaPreviaError(RuntimeError):
    """La copia no se ha podido comprobar; requiere revisión."""


def verificar_copia_previa(carpeta, copia):
    """Compara lógicamente una copia existente con una instalación detenida.

    Conserva ambos orígenes; no publica datos, hashes ni credenciales. Exige
    permisos privados y marcador exacto. No acepta la copia como prueba de
    restauración integral, actualidad futura o exclusión de procesos ajenos.
    """
    try:
        carpeta = Path(carpeta).expanduser().absolute()
        copia = Path(copia).expanduser().absolute()
        verificar_permisos_windows(copia)
        with instalacion_detenida(carpeta):
            rutas = revisar_instalacion(carpeta)
            raiz = carpeta.resolve(strict=True)
            copia_resuelta = copia.resolve(strict=True)
            if copia_resuelta == raiz or raiz in copia_resuelta.parents or copia_resuelta in raiz.parents:
                raise ValueError('La copia debe estar fuera de la instalación.')
            marcador = copia / _MARCADOR
            if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding='utf-8') != _CONTENIDO:
                raise ValueError('Falta un marcador válido.')
            with bloqueo_historial(copia / 'servidor', espera=0):
                verificar_permisos_windows(copia)
                _validar(copia)
                if _huella_estado(copia) != _huella_estado(rutas.datos):
                    raise ValueError('Los datos son diferentes.')
                verificar_permisos_windows(copia)
                if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding='utf-8') != _CONTENIDO:
                    raise ValueError('El marcador ha cambiado.')
        return True
    except Exception:
        raise VerificacionCopiaPreviaError('No se pudo comprobar la copia previa; conserva los datos y revisa el estado.') from None
