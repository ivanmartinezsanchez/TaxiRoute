"""Transición explícita de una instalación local a una LAN nueva.

No elimina ni sobrescribe las instalaciones. El bloqueo es cooperativo: no
impide acceso directo de Docker ni procesos externos. No carga credenciales de
correo ni establece confianza en el certificado de los dispositivos.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3

from taxiroute.arranque_red import _PROYECTO, arrancar_red
from taxiroute.bloquear_transicion_lan import bloquear_local_para_transicion
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.copia_sqlite_experimental import _copiar_archivos, _validar
from taxiroute.iniciar_proxy_local_experimental import _crear_tls_interno
from taxiroute.instalacion_inicial import _generar_certificado, revisar_instalacion
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.recuperar_copia_experimental import _huella_estado
from taxiroute.red_local import comprobar_direccion_local
from taxiroute.reutilizacion_red import reutilizar_red


class TransicionLocalRedError(RuntimeError):
    """Una transición incompleta debe conservarse para revisión."""


@dataclass(frozen=True)
class ResultadoTransicion:
    carpeta: Path
    identidad: Path
    evidencia: object


def transicionar_local_a_red(*, origen, copia, destino, direccion, puerto, proyecto):
    """Bloquea la instalación local y prepara una instalación LAN separada.

    No acepta carpetas existentes. En caso de fallo conserva cualquier marcador,
    datos, certificados y servicios para su revisión; nunca hace rollback. El
    servidor anterior solo queda bloqueado para los lanzadores cooperativos. El
    correo necesita una acción explícita posterior y la prueba no acredita TLS
    confiable en los clientes ni evita acceso simultáneo de procesos externos.
    """
    try:
        origen = Path(origen).expanduser().absolute()
        copia = Path(copia).expanduser().absolute()
        destino = Path(destino).expanduser().absolute()
        comprobar_direccion_local(direccion)
        if (type(puerto) is not int or not 1 <= puerto <= 65535
                or not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto)):
            raise ValueError('Puerto o proyecto inválido.')
        verificar_permisos_windows(destino, permitir_ausente=True)
        padre = destino.parent.resolve(strict=True)
        destino = padre / destino.name
        if os.path.lexists(destino):
            raise ValueError('El destino ya existe.')
        for otra in (origen.resolve(strict=True), copia.resolve(strict=True)):
            if destino == otra or otra in destino.parents or destino in otra.parents:
                raise ValueError('Las carpetas deben estar separadas.')
        if any(os.path.lexists(ruta / '.git') for ruta in (padre, *padre.parents)):
            raise ValueError('El destino debe estar fuera de Git.')
        bloquear_local_para_transicion(origen, copia)
        destino.mkdir(mode=0o700)
        verificar_permisos_windows(destino)
        datos = destino / 'datos'
        datos.mkdir(mode=0o700)
        verificar_permisos_windows(datos)
        rutas_origen = revisar_instalacion(origen)
        with bloqueo_historial(rutas_origen.datos / 'servidor', espera=0):
            with bloqueo_historial(copia / 'servidor', espera=0):
                _validar(copia)
                if _huella_estado(copia) != _huella_estado(rutas_origen.datos):
                    raise ValueError('La copia ha cambiado.')
                _copiar_archivos(copia, datos)
                _validar(datos)
                if _huella_estado(datos) != _huella_estado(rutas_origen.datos):
                    raise ValueError('Los datos copiados no coinciden.')
        # Una copia debe obligar a volver a iniciar sesión y no aceptar PIN
        # o autorizaciones de cambio pendientes de la instalación anterior.
        fecha = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(datos / 'cuentas.sqlite3')) as conexion:
            with conexion:
                conexion.execute('UPDATE sesiones SET revocada_en=? WHERE revocada_en IS NULL', (fecha,))
                conexion.execute('UPDATE desafios_acceso SET invalidado_en=? WHERE invalidado_en IS NULL', (fecha,))
                conexion.execute('UPDATE autorizaciones_cambio SET consumido_en=? WHERE consumido_en IS NULL', (fecha,))
        _validar(datos)
        verificar_permisos_windows(destino)
        cert_externo, clave_externa = _generar_certificado(destino / 'tls', direccion=direccion)
        cert_interno, clave_interna = _crear_tls_interno(destino / 'tls-interno', direccion=direccion)
        identidad = destino / 'proxy-local'
        identidad.mkdir(mode=0o700)
        verificar_permisos_windows(destino)
        evidencia = arrancar_red(identidad=identidad, proyecto=proyecto, direccion=direccion, puerto=puerto,
                                 datos=datos, cert_interno=cert_interno, clave_interna=clave_interna,
                                 cert_externo=cert_externo, clave_externa=clave_externa)
        if reutilizar_red(identidad) != evidencia:
            raise ValueError('La identidad LAN ha cambiado.')
        return ResultadoTransicion(destino, identidad, evidencia)
    except Exception:
        raise TransicionLocalRedError(
            'La transición no se ha confirmado. Conserva los datos, marcadores y servicios para revisión.'
        ) from None
