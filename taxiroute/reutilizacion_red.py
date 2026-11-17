"""Reutilización conservadora de una instalación LAN ya registrada.

No accede al almacén de correo ni crea, elimina o reemplaza contenedores. Los
bloqueos son cooperativos y no excluyen procesos externos ni administradores
Docker; esta función aún no está validada para datos reales.
"""
from pathlib import Path

from taxiroute.arranque_proxy_experimental import _claves_distintas, _validar_certificados
from taxiroute.arranque_red import _esperar_https
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.copia_sqlite_experimental import _validar
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto_red
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.red_local import comprobar_direccion_local
from taxiroute.reutilizacion_proxy_experimental import _bloqueo_instalacion, _exigir_sin_actualizacion_pendiente
from taxiroute.verificador_proxy_experimental import _verificar_composicion, verificar_instalacion_red


class ErrorReutilizacionRed(RuntimeError):
    """El estado de la instalación requiere revisión."""


def reutilizar_red(identidad):
    """Verifica y arranca solo servicios existentes con identidad v2.

    Exige ambos servicios activos o ambos detenidos; rechaza estados mixtos.
    En caso de error conserva lo que exista, incluso un arranque incompleto.
    No carga correo ni garantiza exclusión frente a Docker o procesos ajenos.
    """
    identidad = Path(identidad)
    try:
        with _bloqueo_instalacion(identidad):
            _exigir_sin_actualizacion_pendiente(identidad)
            verificar_permisos_windows(identidad)
            manifiesto = leer_manifiesto_red(identidad)
            rutas = {nombre: Path(ruta) for nombre, ruta in manifiesto.rutas.items()}
            for ruta in (rutas['datos'], rutas['cert_interno'].parent, rutas['cert_externo'].parent):
                verificar_permisos_windows(ruta)
            comprobar_direccion_local(manifiesto.direccion)
            _validar_certificados(rutas['cert_interno'], rutas['clave_interna'], interno=True,
                                  direccion=manifiesto.direccion)
            _validar_certificados(rutas['cert_externo'], rutas['clave_externa'], interno=False,
                                  direccion=manifiesto.direccion)
            _claves_distintas(rutas['cert_interno'], rutas['cert_externo'])
            opciones = dict(proyecto=manifiesto.proyecto, imagenes_esperadas=manifiesto.imagenes,
                            rutas_esperadas=rutas, direccion=manifiesto.direccion, puerto=manifiesto.puerto,
                            permitir_detenidos=True, puerto_no_observable_detenido=True)
            def comprobar_estado():
                _exigir_sin_actualizacion_pendiente(identidad)
                if leer_manifiesto_red(identidad) != manifiesto:
                    raise ValueError('Identidad modificada.')
                actual = _verificar_composicion(**opciones)
                if (actual.backend.identificador != manifiesto.contenedores['backend']
                        or actual.proxy.identificador != manifiesto.contenedores['proxy']):
                    raise ValueError('Contenedores distintos.')
                return actual
            estado = comprobar_estado()
            if estado.backend.activo != estado.proxy.activo:
                raise ValueError('Servicios en estado mixto.')
            if not estado.backend.activo:
                if _docker(['ps', '--quiet', '--no-trunc']).strip():
                    raise ValueError('Hay otros contenedores activos.')
                with bloqueo_historial(rutas['datos'] / 'servidor', espera=0):
                    _validar(rutas['datos'])
                if comprobar_estado() != estado or _docker(['ps', '--quiet', '--no-trunc']).strip():
                    raise ValueError('Estado modificado.')
                comprobar_direccion_local(manifiesto.direccion)
                _docker(['start', estado.backend.identificador])
                intermedio = comprobar_estado()
                if (not intermedio.backend.activo or intermedio.proxy != estado.proxy
                        or intermedio.backend.imagen != estado.backend.imagen):
                    raise ValueError('Estado inesperado al iniciar backend.')
                _docker(['start', estado.proxy.identificador])
            evidencia = verificar_instalacion_red(identidad)
            _esperar_https(rutas['cert_externo'], manifiesto.direccion, manifiesto.puerto)
            if comprobar_estado() != evidencia or verificar_instalacion_red(identidad) != evidencia:
                raise ValueError('Estado modificado tras comprobar HTTPS.')
            return evidencia
    except Exception:
        raise ErrorReutilizacionRed('No se pudo reutilizar la instalación de red; conserva sus servicios y datos para revisión.') from None
