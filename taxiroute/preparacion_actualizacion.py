"""Preparación interna para una futura actualización local, sin aplicarla.

No construye imágenes ni inicia, detiene o reemplaza contenedores. Los bloqueos
solo coordinan procesos que los respetan; no protege frente a administradores,
Docker ni procesos externos que alteren la instalación simultáneamente.
"""
from contextlib import contextmanager
import os
from pathlib import Path

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.copia_sqlite_experimental import _validar
from taxiroute.instalacion_inicial import revisar_instalacion
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION, _bloqueo_instalacion
from taxiroute.verificador_proxy_experimental import verificar_para_reutilizar


class PreparacionActualizacionError(RuntimeError):
    pass


@contextmanager
def instalacion_detenida(carpeta):
    """Valida precondiciones y mantiene bloqueos hasta terminar el contexto.

    Rechaza todos los contenedores activos como medida conservadora. La
    comprobación no es atómica respecto a procesos ajenos a los bloqueos. El
    resultado no autoriza todavía a reemplazar imágenes ni actualizar bases.
    """
    entregado = False
    try:
        verificar_permisos_windows(carpeta)
        rutas = revisar_instalacion(carpeta)
        identidad = rutas.carpeta / "proxy-local"
        with _bloqueo_instalacion(identidad), bloqueo_historial(rutas.datos / "servidor", espera=0):
            if os.path.lexists(identidad / MARCADOR_ACTUALIZACION):
                raise PreparacionActualizacionError("Una actualización anterior requiere revisión.")
            manifiesto = leer_manifiesto(identidad)
            if Path(manifiesto.rutas["datos"]).resolve(strict=True) != rutas.datos.resolve(strict=True):
                raise PreparacionActualizacionError("Los datos no coinciden con la instalación.")
            opciones = dict(proyecto=manifiesto.proyecto, imagenes_esperadas=manifiesto.imagenes,
                rutas_esperadas={k: Path(v) for k, v in manifiesto.rutas.items()},
                contenedores_esperados=manifiesto.contenedores)
            antes = verificar_para_reutilizar(**opciones)
            if antes.backend.activo or antes.proxy.activo or _docker(["ps", "--quiet", "--no-trunc"]).strip():
                raise PreparacionActualizacionError("Detén los contenedores antes de continuar.")
            _validar(rutas.datos)
            if verificar_para_reutilizar(**opciones) != antes or _docker(["ps", "--quiet", "--no-trunc"]).strip():
                raise PreparacionActualizacionError("Los servicios cambiaron durante la comprobación.")
            entregado = True
            yield manifiesto
    except PreparacionActualizacionError:
        raise
    except Exception:
        if entregado:
            raise
        raise PreparacionActualizacionError("No se pudo verificar la instalación; requiere revisión.") from None
