"""Liberación experimental de una carrera ya conciliada en el mismo proceso.

El historial, la base y la flota deben pertenecer a la misma instalación de
confianza. No expone una ruta web ni coordina procesos independientes.
"""
from contextlib import closing
import os
from pathlib import Path

from taxiroute.conciliacion_csv import _conectar_existente, _leer
from taxiroute.conciliacion_sqlite import _abrir_historial, _comprobar_fila
from taxiroute.finalizacion_pendiente import resumen_para_historial
from taxiroute.flota import FlotaBloqueada, OperacionNoPermitida
from taxiroute.historial import _fila
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def liberar_cierre_conciliado(flota, sesiones, token, ruta_historial):
    """Verifica la cuenta y ambos archivos antes de liberar solo su carrera.

    El historial debe ser el configurado por el servidor, no por el cliente.
    Orden de bloqueos: historial, recuperación, flota. Un reinicio pierde el
    marcador en memoria y requiere otro procedimiento de revisión.
    """
    if not flota._modo_durable_experimental:
        raise OperacionNoPermitida("Se necesita el modo durable experimental.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    base, historial = Path(sesiones.ruta), Path(ruta_historial)
    if base.resolve() == historial.resolve() or (base.exists() and historial.exists() and os.path.samefile(base, historial)):
        raise OperacionNoPermitida("La base y el historial deben ser distintos.")
    with closing(_abrir_historial(historial)) as h:
        h.execute("BEGIN IMMEDIATE")
        try:
            HistorialSQLite._verificar(h, permitir_creacion=False)
            with closing(_conectar_existente(base)) as c:
                c.execute("BEGIN IMMEDIATE")
                try:
                    sesion = c.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                    identidad = _identidad_vigente(c, sesion, sesiones._reloj())
                    if identidad is None or identidad.rol not in {"conductor", "administrador"}:
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
                        if c.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario,)).fetchone():
                            raise OperacionNoPermitida("Hay una operación pendiente de revisión.")
                        _, estado, resumen = _leer(c, usuario, punto.carrera_id)
                        esperado = resumen_para_historial(pendiente)
                        if estado != "guardada" or _fila(resumen) != _fila(esperado):
                            raise OperacionNoPermitida("El cierre todavía no coincide con el registro guardado.")
                        _comprobar_fila(h, _fila(esperado), usuario, permitir_insertar=False)
                        # No recalcular el cierre ni guardar de nuevo. Mantener los
                        # bloqueos hasta actualizar memoria y retirar el marcador.
                        carrera_anterior = servicio.carrera
                        resumen_anterior = servicio.ultimo_resumen
                        revision_anterior = flota._revisiones.get(usuario, 0)
                        try:
                            servicio.ultimo_resumen = esperado
                            servicio.carrera = None
                            flota._revisiones[usuario] = punto.revision
                            del flota._cierres_preparados[usuario]
                            flota._bloqueados.remove(usuario)
                            # Soltar las transacciones antes del bloqueo de flota.
                            # Si fallan, restaurar memoria antes de permitir acciones.
                            c.rollback()
                            h.rollback()
                        except BaseException:
                            servicio.carrera = carrera_anterior
                            servicio.ultimo_resumen = resumen_anterior
                            flota._revisiones[usuario] = revision_anterior
                            flota._cierres_preparados[usuario] = pendiente
                            flota._bloqueados.add(usuario)
                            raise
                        return esperado
                finally:
                    if c.in_transaction:
                        c.rollback()
        finally:
            if h.in_transaction:
                h.rollback()
