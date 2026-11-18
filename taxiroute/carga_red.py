"""Carga explícita de credenciales en una composición de red verificada.

No consulta el almacén ni envía correos. Las verificaciones no son atómicas
frente a administradores de Docker o procesos que ignoren los bloqueos.
"""
from pathlib import Path

from taxiroute.cargar_gmail_windows import CARGADOR, _docker, _estado_archivo
from taxiroute.carga_proxy_experimental import CargaProxyAntesEnvio, CargaProxyPosibleExposicion
from taxiroute.manifiesto_proxy_experimental import leer_manifiesto_red
from taxiroute.oauth_gmail_contenedor import _validar_contenido
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.reutilizacion_proxy_experimental import _bloqueo_instalacion, _exigir_sin_actualizacion_pendiente
from taxiroute.verificador_proxy_experimental import EstadoComposicion, verificar_instalacion_red


def cargar_correo_red(*, identidad, evidencia_esperada, proveedor):
    """Envía un perfil suministrado explícitamente tras verificar el destino.

    El llamador debe haber comprobado el canal HTTPS y autorizado el proveedor.
    Una excepción tras comenzar el transporte puede implicar exposición parcial.
    No establece confianza TLS del cliente ni autoriza el uso con datos reales.
    """
    if not callable(proveedor) or not isinstance(evidencia_esperada, EstadoComposicion):
        raise CargaProxyAntesEnvio("No se pudo preparar la carga de correo.")
    identidad = Path(identidad)
    contenido = datos = None
    envio_iniciado = False
    try:
        with _bloqueo_instalacion(identidad):
            _exigir_sin_actualizacion_pendiente(identidad)
            verificar_permisos_windows(identidad)
            manifiesto = leer_manifiesto_red(identidad)
            def comprobar_privacidad():
                _exigir_sin_actualizacion_pendiente(identidad)
                verificar_permisos_windows(identidad)
                for ruta in (Path(manifiesto.rutas['datos']), Path(manifiesto.rutas['cert_interno']).parent,
                             Path(manifiesto.rutas['cert_externo']).parent):
                    verificar_permisos_windows(ruta)
            comprobar_privacidad()
            inicial = verificar_instalacion_red(identidad)
            if inicial != evidencia_esperada or _estado_archivo(inicial.backend.identificador) != 'ausente':
                raise ValueError("Destino distinto o correo ya cargado.")
            contenido = proveedor()
            if not isinstance(contenido, str):
                raise ValueError("Perfil no válido.")
            datos = contenido.encode('utf-8')
            _validar_contenido(datos)
            comprobar_privacidad()
            if (leer_manifiesto_red(identidad) != manifiesto
                    or verificar_instalacion_red(identidad) != inicial
                    or _estado_archivo(inicial.backend.identificador) != 'ausente'):
                raise ValueError("El destino ha cambiado.")
            envio_iniciado = True
            _docker(['exec', '-i', '--user', '10001:10001', '--workdir', '/app',
                     inicial.backend.identificador, *CARGADOR], entrada=datos)
            comprobar_privacidad()
            if (leer_manifiesto_red(identidad) != manifiesto
                    or verificar_instalacion_red(identidad) != inicial
                    or _estado_archivo(inicial.backend.identificador) != 'presente'):
                raise ValueError("No se pudo confirmar el destino.")
            return inicial
    except Exception:
        if envio_iniciado:
            raise CargaProxyPosibleExposicion("No se pudo confirmar la carga; pudo entregarse parcialmente.") from None
        raise CargaProxyAntesEnvio("No se pudo preparar la carga; no se intentó el envío.") from None
    finally:
        contenido = datos = None
