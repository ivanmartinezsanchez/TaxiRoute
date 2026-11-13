"""Reutilización aislada de una composición proxy; solo para pruebas ficticias.

No acredita la procedencia de imágenes ni protege frente al administrador de
Docker. Puede detener el proxy cuando el backend está detenido; no recrea ni
borra servicios. No lee el almacén de Windows.
"""
from dataclasses import dataclass, replace
import os
from pathlib import Path

from taxiroute.arranque_proxy_experimental import (
    ErrorArranqueProxy, _claves_distintas, _esperar_https, _validar_certificados, arrancar_proxy,
)
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, CargaProxyPosibleExposicion, cargar_proxy_experimental
from taxiroute.cargar_gmail_windows import _docker, _estado_archivo
from taxiroute.manifiesto_proxy_experimental import ARCHIVO, MARCADOR, crear_manifiesto, leer_manifiesto
from taxiroute.verificador_proxy_experimental import verificar_composicion, verificar_para_reutilizar


class ErrorReutilizacion(RuntimeError):
    """La instalación necesita revisión antes de continuar."""


@dataclass(frozen=True)
class ResultadoReutilizacion:
    evidencia: object
    correo: str
    servicios_recuperados: bool = False


def _bloqueo_instalacion(carpeta):
    carpeta = Path(carpeta).expanduser().absolute()
    if not carpeta.is_dir() or any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                                   for p in (carpeta, *carpeta.parents)):
        raise ValueError("Carpeta no válida.")
    carpeta = carpeta.resolve(strict=True)
    if any(os.path.lexists(p / ".git") for p in (carpeta, *carpeta.parents)):
        raise ValueError("La carpeta debe estar fuera de Git.")
    cerrojo = carpeta / ".reutilizacion-proxy.lock"
    if cerrojo.is_symlink() or (hasattr(cerrojo, "is_junction") and cerrojo.is_junction()):
        raise ValueError("Bloqueo no válido.")
    return bloqueo_historial(carpeta / ".reutilizacion-proxy", espera=0)


def iniciar_y_registrar(carpeta, *, proyecto, rutas):
    """Serializa los intentos que comparten esta carpeta de instalación."""
    try:
        with _bloqueo_instalacion(carpeta):
            return _iniciar_y_registrar(carpeta, proyecto=proyecto, rutas=rutas)
    except ErrorReutilizacion:
        raise
    except Exception:
        raise ErrorReutilizacion("No se pudo bloquear la instalación para iniciarla.") from None


def _iniciar_y_registrar(carpeta, *, proyecto, rutas):
    """Primer inicio aislado; si falla el registro, conserva los servicios."""
    try:
        carpeta = Path(carpeta)
        if not carpeta.is_dir() or carpeta.is_symlink() or (hasattr(carpeta, "is_junction") and carpeta.is_junction()):
            raise ValueError("Carpeta no válida.")
        carpeta = carpeta.resolve(strict=True)
        if any(os.path.lexists(p / ".git") for p in (carpeta, *carpeta.parents)):
            raise ValueError("La carpeta debe estar fuera de Git.")
        if os.path.lexists(carpeta / ARCHIVO) or os.path.lexists(carpeta / MARCADOR):
            raise ValueError("Ya existe un registro.")
        arranque = arrancar_proxy(proyecto=proyecto, **rutas)
        imagenes = {"backend": arranque.imagen_backend, "proxy": arranque.imagen_proxy}
        ids = {"backend": arranque.evidencia.backend.identificador,
               "proxy": arranque.evidencia.proxy.identificador}
        verificar_composicion(proyecto=proyecto, imagenes_esperadas=imagenes,
                              rutas_esperadas=rutas, anterior=arranque.evidencia)
        return crear_manifiesto(carpeta, proyecto=proyecto, imagenes=imagenes,
                               contenedores=ids, rutas=rutas)
    except Exception:
        raise ErrorReutilizacion("No se pudo registrar la instalación; no se han detenido los servicios.") from None


def reutilizar(carpeta, *, proveedor=None):
    """Serializa los intentos que comparten esta carpeta de instalación."""
    try:
        with _bloqueo_instalacion(carpeta):
            return _reutilizar(carpeta, proveedor=proveedor)
    except (ErrorReutilizacion, CargaProxyPosibleExposicion):
        raise
    except Exception:
        raise ErrorReutilizacion("No se pudo bloquear la instalación para reutilizarla.") from None


def _reutilizar(carpeta, *, proveedor=None):
    """Verifica la instalación e inicia servicios detenidos en orden.

    Si solo el backend está detenido, detiene el proxy tras verificarlo y los
    inicia en orden. Si ambos están activos pero HTTPS no responde, comprueba
    que no hayan cambiado y reinicia solo el proxy. El proveedor, si se
    suministra, solo se consulta después de validar HTTPS y el destino.
    """
    try:
        m = leer_manifiesto(carpeta)
        rutas = {k: Path(v) for k, v in m.rutas.items()}
        _validar_certificados(rutas["cert_interno"], rutas["clave_interna"], interno=True)
        _validar_certificados(rutas["cert_externo"], rutas["clave_externa"], interno=False)
        _claves_distintas(rutas["cert_interno"], rutas["cert_externo"])
        opciones = dict(proyecto=m.proyecto, imagenes_esperadas=m.imagenes, rutas_esperadas=rutas)
        estado = verificar_para_reutilizar(**opciones, contenedores_esperados=m.contenedores)
        servicios_recuperados = False
        if not estado.backend.activo or not estado.proxy.activo:
            if verificar_para_reutilizar(**opciones, contenedores_esperados=m.contenedores) != estado:
                raise ErrorReutilizacion("La instalación cambió durante la comprobación.")
        if not estado.backend.activo and estado.proxy.activo:
            # El backend está detenido; el proxy no puede completar operaciones.
            _docker(["stop", "--time", "3", estado.proxy.identificador])
            servicios_recuperados = True
            despues = verificar_para_reutilizar(**opciones, contenedores_esperados=m.contenedores)
            if (despues.backend != estado.backend or despues.proxy != replace(estado.proxy, activo=False)):
                raise ErrorReutilizacion("La instalación cambió al detener el proxy.")
            estado = despues
        if not estado.backend.activo:
            _docker(["start", estado.backend.identificador])
            servicios_recuperados = True
        if not estado.proxy.activo:
            _docker(["start", estado.proxy.identificador])
            servicios_recuperados = True
        evidencia = verificar_composicion(**opciones)
        if (evidencia.backend.identificador != m.contenedores["backend"]
                or evidencia.proxy.identificador != m.contenedores["proxy"]):
            raise ErrorReutilizacion("Los contenedores han cambiado; requiere revisión.")
        try:
            _esperar_https(rutas["cert_externo"])
        except ErrorArranqueProxy:
            if not (estado.backend.activo and estado.proxy.activo):
                raise
            # Tras perder HTTPS, no reiniciar el backend ni asumir que la
            # inspección y el reinicio forman una operación atómica.
            verificar_composicion(**opciones, anterior=evidencia)
            previo = evidencia
            _docker(["restart", "--time", "3", previo.proxy.identificador])
            servicios_recuperados = True
            evidencia = verificar_composicion(**opciones)
            if (evidencia.backend != previo.backend
                    or evidencia.proxy.identificador != previo.proxy.identificador
                    or evidencia.proxy.imagen != previo.proxy.imagen):
                raise ErrorReutilizacion("La instalación cambió al reiniciar el proxy.")
            _esperar_https(rutas["cert_externo"])
        evidencia = verificar_composicion(**opciones, anterior=evidencia)
        correo = _estado_archivo(evidencia.backend.identificador)
        if correo == "ausente" and proveedor is not None:
            evidencia = cargar_proxy_experimental(**opciones, evidencia_esperada=evidencia, proveedor=proveedor)
            correo = "cargado"
        return ResultadoReutilizacion(evidencia, correo, servicios_recuperados)
    except (ErrorReutilizacion, CargaProxyPosibleExposicion):
        raise
    except CargaProxyAntesEnvio:
        raise ErrorReutilizacion("No se cargó el correo: la comprobación falló antes de enviarlo. Revisa permisos y autorización.") from None
    except Exception:
        raise ErrorReutilizacion("No se pudo completar la reutilización; revisa el estado de los servicios antes de reintentar. No se han borrado.") from None
