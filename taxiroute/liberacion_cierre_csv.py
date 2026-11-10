"""Liberación experimental de cierres conciliados en CSV por cuenta.

No expone rutas web. El directorio de historiales procede de la configuración
del servidor y debe permanecer bajo control de una única instancia cooperativa.
"""
from contextlib import closing
import os
from pathlib import Path

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.conciliacion_csv import _conectar_existente, _leer, _verificar_csv
from taxiroute.finalizacion_pendiente import resumen_para_historial
from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida
from taxiroute.historial import _fila
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def liberar_cierre_csv(flota, sesiones, token, carpeta_historial):
    """Libera solo el cierre de la sesión tras verificar su CSV y su estado.

    Orden de bloqueos: CSV, cuentas, flota. Los archivos y las modificaciones
    que ignoren el bloqueo cooperativo no forman una transacción atómica.
    No crea el CSV, no acepta una ruta aportada por el navegador y no recupera
    por sí solo cierres tras reiniciar el proceso.
    """
    if not flota._modo_durable_experimental:
        raise OperacionNoPermitida("Se necesita el modo durable experimental.")
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
                sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                if (identidad is None or identidad.rol not in {"conductor", "administrador"}
                        or identidad.usuario_id != identidad_inicial.usuario_id):
                    raise SesionNoAutorizada()
                usuario = identidad.usuario_id
                with flota._bloqueo:
                    pendiente = flota._cierres_preparados.get(usuario)
                    servicio = flota._servicios.get(usuario)
                    if usuario not in flota._bloqueados or pendiente is None or servicio is None or servicio.carrera is None:
                        raise FlotaBloqueada("No hay un cierre verificable en este proceso.")
                    punto = pendiente.punto
                    if (punto.usuario_id != usuario or flota._revisiones.get(usuario, 0) != punto.revision - 1
                            or servicio.pendiente is not None):
                        raise OperacionNoPermitida("La carrera en memoria no coincide con el cierre.")
                    captura = servicio.carrera.capturar()
                    if (captura.identificador != punto.carrera_id or captura.estado.value != punto.estado_vehiculo
                            or captura.inicio != punto.inicio
                            or format(captura.tarifa_parado, "f") != punto.tarifa_parado
                            or format(captura.tarifa_movimiento, "f") != punto.tarifa_movimiento):
                        raise OperacionNoPermitida("La carrera en memoria cambió y necesita revisión.")
                    if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario,)).fetchone():
                        raise OperacionNoPermitida("Hay una operación pendiente de revisión.")
                    _, estado, resumen = _leer(conexion, usuario, punto.carrera_id)
                    esperado = resumen_para_historial(pendiente)
                    if estado != "guardada" or _fila(resumen) != _fila(esperado):
                        raise OperacionNoPermitida("El cierre todavía no coincide con el registro guardado.")
                    _verificar_csv(historial, esperado)
                    carrera_anterior, resumen_anterior = servicio.carrera, servicio.ultimo_resumen
                    revision_anterior = flota._revisiones.get(usuario, 0)
                    try:
                        servicio.ultimo_resumen = esperado
                        servicio.carrera = None
                        flota._revisiones[usuario] = punto.revision
                        del flota._cierres_preparados[usuario]
                        flota._bloqueados.remove(usuario)
                        conexion.rollback()
                    except BaseException:
                        servicio.carrera, servicio.ultimo_resumen = carrera_anterior, resumen_anterior
                        flota._revisiones[usuario] = revision_anterior
                        flota._cierres_preparados[usuario] = pendiente
                        flota._bloqueados.add(usuario)
                        raise
                    return esperado
            finally:
                if conexion.in_transaction:
                    conexion.rollback()
