"""Liberación experimental de una carrera recuperada y conciliada en CSV."""
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import _conectar_existente, _leer, _verificar_csv
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.intenciones_operacion import _validar_base
from taxiroute.puntos_control import _desde_fila
from taxiroute.recuperacion_ultima_captura import huella_revision
from taxiroute.revision_durable import revision_durable
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def liberar_recuperada_csv(sesiones, token, flota, carpeta_historial, *,
                           revision_esperada, ultimo_control_esperado, huella_esperada):
    """Confirma la fila propia y libera el bloqueo tras revisar la captura.

    El directorio procede del servidor, nunca del navegador. La huella, la
    revisión y el instante deben corresponder al punto mostrado previamente.
    Orden de bloqueo: CSV, cuentas, flota. No hace atómicos ambos archivos.
    """
    if not isinstance(flota, FlotaEnMemoria) or not flota._modo_durable_experimental:
        raise OperacionNoPermitida("Se necesita una flota experimental de recuperación.")
    if (type(revision_esperada) is not int or not 0 <= revision_esperada < 2**63 - 1
            or not isinstance(ultimo_control_esperado, datetime)
            or ultimo_control_esperado.tzinfo is None or ultimo_control_esperado.utcoffset() is None
            or not isinstance(huella_esperada, str) or len(huella_esperada) != 64
            or any(c not in "0123456789abcdef" for c in huella_esperada)):
        raise OperacionNoPermitida("Los datos de revisión no son válidos.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    identidad_inicial = sesiones.consultar(token)
    if identidad_inicial is None or identidad_inicial.rol not in {"administrador", "conductor"}:
        raise SesionNoAutorizada()
    carpeta = Path(carpeta_historial)
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise OperacionNoPermitida("La carpeta de historiales no está disponible.")
    historial = _historial_de_cuenta(carpeta.resolve(), identidad_inicial.usuario_id)
    base, csv = Path(sesiones.ruta), historial.ruta
    if (not csv.is_file() or csv.is_symlink() or base.resolve() == csv.resolve()
            or (base.exists() and os.path.samefile(base, csv))):
        raise OperacionNoPermitida("El historial no está disponible o no es independiente.")
    with bloqueo_historial(csv):
        with closing(_conectar_existente(base)) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                _validar_base(conexion)
                sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                if (identidad is None or identidad.rol not in {"administrador", "conductor"}
                        or identidad.usuario_id != identidad_inicial.usuario_id):
                    raise SesionNoAutorizada()
                usuario = identidad.usuario_id
                with flota._bloqueo:
                    servicio = flota._servicios.get(usuario)
                    if servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None):
                        raise FlotaBloqueada("Hay una carrera en memoria que necesita revisión.")
                    if usuario in flota._cierres_preparados:
                        raise FlotaBloqueada("Hay un cierre en memoria que necesita revisión.")
                    if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario,)).fetchone():
                        raise FlotaBloqueada("Hay una intención sin resolver.")
                    if conexion.execute("""SELECT 1 FROM carreras_recuperacion WHERE usuario_id=?
                        AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')""", (usuario,)).fetchone():
                        raise FlotaBloqueada("Hay una carrera sin resolver.")
                    filas = conexion.execute("""SELECT * FROM carreras_recuperacion WHERE usuario_id=?
                        AND estado_registro='guardada' AND revision=?""", (usuario, revision_esperada + 1)).fetchall()
                    if len(filas) != 1 or revision_durable(conexion, usuario) != revision_esperada + 1:
                        raise OperacionNoPermitida("El cierre no coincide con la última revisión guardada.")
                    _, estado, resumen = _leer(conexion, usuario, filas[0]["id"])
                    if (huella_revision(_desde_fila(filas[0]), revision_esperada) != huella_esperada
                            or estado != "guardada" or resumen.fin != ultimo_control_esperado):
                        raise OperacionNoPermitida("El cierre no coincide con el punto revisado.")
                    _verificar_csv(historial, resumen)
                    conexion.rollback()
                    flota._revisiones[usuario] = revision_esperada + 1
                    flota._servicios.pop(usuario, None)
                    flota._bloqueados.discard(usuario)
                    return resumen
            finally:
                if conexion.in_transaction:
                    conexion.rollback()
