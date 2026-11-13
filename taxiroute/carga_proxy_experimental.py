"""Carga experimental para proxy; aún sin integración con almacén ni lanzador.

El proveedor debe facilitarse explícitamente. La secuencia no constituye una
autorización de uso real: exige identidades obtenidas por construcción controlada,
y sus inspecciones no son atómicas ni protegen frente al administrador Docker.
"""
from taxiroute.cargar_gmail_windows import CARGADOR, _docker, _estado_archivo
from taxiroute.oauth_gmail_contenedor import _validar_contenido
from taxiroute.verificador_proxy_experimental import EstadoComposicion, verificar_composicion


class CargaProxyAntesEnvio(RuntimeError):
    """La operación se detuvo antes de intentar transmitir."""


class CargaProxyPosibleExposicion(RuntimeError):
    """Se intentó transmitir y no puede descartarse una entrega parcial."""


def cargar_proxy_experimental(*, proyecto, imagenes_esperadas, rutas_esperadas,
                             evidencia_esperada, proveedor):
    """Transmite por stdin solo tras dos verificaciones; no consulta almacenes.

    El proveedor inyectado devuelve el contenido en texto. La evidencia previa
    es obligatoria y se compara antes de leer el proveedor. Devuelve la evidencia
    de la composición verificada. Una modificación posterior a la verificación
    no puede descartarse; no se debe usar con credenciales reales todavía.
    """
    if not callable(proveedor) or not isinstance(evidencia_esperada, EstadoComposicion):
        raise CargaProxyAntesEnvio("No se pudo preparar la carga experimental.")
    try:
        # Aislar las opciones de cambios del proveedor durante la lectura.
        opciones = dict(proyecto=proyecto, imagenes_esperadas=dict(imagenes_esperadas),
                        rutas_esperadas=dict(rutas_esperadas))
        inicial = verificar_composicion(**opciones, anterior=evidencia_esperada)
        if inicial != evidencia_esperada:
            raise ValueError
        identificador = inicial.backend.identificador
        if _estado_archivo(identificador) != "ausente":
            raise ValueError
        contenido = proveedor()
        if not isinstance(contenido, str):
            raise ValueError
        datos = contenido.encode("utf-8")
        _validar_contenido(datos)
        previa = verificar_composicion(**opciones, anterior=inicial)
        if previa != inicial or _estado_archivo(identificador) != "ausente":
            raise ValueError
    except Exception:
        raise CargaProxyAntesEnvio("No se pudo preparar la carga experimental; no se intentó el envío.") from None

    # Una excepción del transporte también puede ocurrir tras una entrega parcial.
    try:
        _docker(["exec", "-i", "--user", "10001:10001", "--workdir", "/app",
                 identificador, *CARGADOR], entrada=datos)
        posterior = verificar_composicion(**opciones, anterior=inicial)
        if posterior != inicial or _estado_archivo(identificador) != "presente":
            raise ValueError
    except Exception:
        raise CargaProxyPosibleExposicion(
            "No se pudo confirmar la carga experimental; el envío pudo haberse realizado."
        ) from None
    finally:
        contenido = None
        datos = None
    return posterior
