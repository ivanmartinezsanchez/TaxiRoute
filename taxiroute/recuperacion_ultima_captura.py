"""Revisión y cierre experimental tras reinicio con último punto conocido.

El integrador debe garantizar una sola instancia propietaria de la base. El
bloqueo de flota no coordina procesos distintos. La demo web persistente
lo utiliza bajo su bloqueo exclusivo. No reanuda carreras ni cobra tiempo
posterior al último punto guardado.
"""
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path

from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.finalizacion_pendiente import (
    ResumenPendiente, _finalizar_en_transaccion, resumen_para_historial,
)
from taxiroute.flota import FlotaEnMemoria, FlotaBloqueada, OperacionNoPermitida
from taxiroute.intenciones_operacion import _validar_base
from taxiroute.puntos_control import _desde_fila
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def huella_revision(punto, revision):
    """Identifica los datos mostrados para detectar cambios antes de confirmar."""
    from dataclasses import replace
    from hashlib import sha256
    from taxiroute.recibos_operacion import _codificar
    if type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise OperacionNoPermitida("La revisión no es válida.")
    return sha256(_codificar(replace(punto, revision=revision)).encode("utf-8")).hexdigest()


@contextmanager
def _acceso(sesiones, token, flota):
    if not isinstance(flota, FlotaEnMemoria) or not flota._modo_durable_experimental:
        raise OperacionNoPermitida("Se necesita una flota experimental de recuperación.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    with closing(_conectar_existente(sesiones.ruta)) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            _validar_base(conexion)
            sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
            identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
            if identidad is None or identidad.rol not in {"administrador", "conductor"}:
                raise SesionNoAutorizada()
            usuario_id = identidad.usuario_id
            with flota._bloqueo:
                if usuario_id not in flota._bloqueados:
                    raise OperacionNoPermitida("La cuenta no está bloqueada para recuperación.")
                servicio = flota._servicios.get(usuario_id)
                if servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None):
                    raise FlotaBloqueada("Hay una carrera en memoria que requiere revisión.")
                if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario_id,)).fetchone():
                    raise FlotaBloqueada("Hay una intención sin resolver.")
                filas = conexion.execute("""SELECT * FROM carreras_recuperacion WHERE usuario_id=?
                    AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')""",
                    (usuario_id,)).fetchall()
                if len(filas) != 1 or filas[0]["estado_registro"] not in {"activa", "revision_pendiente", "guardado_pendiente"}:
                    raise OperacionNoPermitida("No hay una carrera recuperable sin ambigüedades.")
                yield conexion, usuario_id, filas[0]
        except BaseException:
            conexion.rollback()
            raise


def revisar_ultima_captura(sesiones, token, flota):
    """Devuelve el punto propio guardado y el estado, sin cambiar los datos."""
    with _acceso(sesiones, token, flota) as (conexion, _, fila):
        punto = _desde_fila(fila)
        estado = fila["estado_registro"]
        if estado == "guardado_pendiente":
            resumen = ResumenPendiente(punto, datetime.fromisoformat(fila["fin"]), Decimal(fila["importe_final"]))
            resumen_para_historial(resumen)
        conexion.rollback()
        return punto, estado


def cerrar_en_ultima_captura(sesiones, token, flota, *, revision_esperada, ultimo_control_esperado, huella_esperada=None):
    """Prepara el cierre propio en la última captura, sin estimar tiempo perdido.

    La revisión y fecha corresponden al punto previamente mostrado al usuario.
    Un reintento devuelve el mismo resumen pendiente. El historial y el
    desbloqueo se realizan en un proceso posterior de conciliación.
    """
    if (type(revision_esperada) is not int or not 0 <= revision_esperada < 2**63 - 1
            or not isinstance(ultimo_control_esperado, datetime)
            or ultimo_control_esperado.tzinfo is None
            or ultimo_control_esperado.utcoffset() is None):
        raise OperacionNoPermitida("La revisión o el instante de confirmación no son válidos.")
    with _acceso(sesiones, token, flota) as (conexion, usuario_id, fila):
        punto = _desde_fila(fila)
        revision_actual = revision_esperada + (fila["estado_registro"] == "guardado_pendiente")
        if (punto.revision != revision_actual or punto.ultimo_control != ultimo_control_esperado
                or (huella_esperada is not None and huella_revision(punto, revision_esperada) != huella_esperada)):
            raise OperacionNoPermitida("El punto ha cambiado; revísalo antes de confirmar.")
        if fila["estado_registro"] == "guardado_pendiente":
            resumen = ResumenPendiente(punto, datetime.fromisoformat(fila["fin"]), Decimal(fila["importe_final"]))
            resumen_para_historial(resumen)
            conexion.rollback()
            flota._revisiones[usuario_id] = punto.revision
            return resumen
        if punto.revision >= 2**63 - 1:
            raise OperacionNoPermitida("La revisión no admite un cierre nuevo.")
        actualizado = conexion.execute("""UPDATE carreras_recuperacion SET revision=revision+1
            WHERE id=? AND usuario_id=? AND estado_registro=? AND revision=?""",
            (punto.carrera_id, usuario_id, fila["estado_registro"], punto.revision))
        if actualizado.rowcount != 1:
            raise OperacionNoPermitida("No se pudo preparar la revisión del cierre.")
        fila_actual = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                       (punto.carrera_id, usuario_id)).fetchone()
        punto_final = _desde_fila(fila_actual)
        from dataclasses import replace
        if punto_final != replace(punto, revision=punto.revision + 1):
            raise OperacionNoPermitida("La captura ha cambiado durante la recuperación.")
        if fila["estado_registro"] == "revision_pendiente":
            resumen = _finalizar_en_transaccion(conexion, punto_final, punto_final.ultimo_control,
                                                permitir_revision=True)
        else:
            resumen = _finalizar_en_transaccion(conexion, punto_final, punto_final.ultimo_control)
        conexion.commit()
        flota._revisiones[usuario_id] = punto_final.revision
        return resumen


def liberar_recuperada(sesiones, token, flota, ruta_historial, *,
                       revision_esperada, ultimo_control_esperado, huella_esperada=None):
    """Libera una recuperación confirmada, sin aceptar IDs ni rutas del cliente.

    La ruta es configuración confiable del servidor. Solo para instancia única.
    Comprueba historial -> cuentas -> flota; un reintento idéntico es seguro.
    """
    from taxiroute.conciliacion_csv import _leer
    from taxiroute.conciliacion_sqlite import _abrir_historial, _comprobar_fila
    from taxiroute.historial import _fila
    from taxiroute.historial_sqlite import HistorialSQLite
    from taxiroute.revision_durable import revision_durable

    if not isinstance(flota, FlotaEnMemoria) or not flota._modo_durable_experimental:
        raise OperacionNoPermitida("Se necesita una flota experimental de recuperación.")
    if (type(revision_esperada) is not int or not 0 <= revision_esperada < 2**63 - 1
            or not isinstance(ultimo_control_esperado, datetime)
            or ultimo_control_esperado.tzinfo is None
            or ultimo_control_esperado.utcoffset() is None):
        raise OperacionNoPermitida("La revisión o el instante de confirmación no son válidos.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    base, historial = Path(sesiones.ruta), Path(ruta_historial)
    if base.resolve() == historial.resolve() or (
            base.exists() and historial.exists() and os.path.samefile(base, historial)):
        raise OperacionNoPermitida("El historial y las cuentas deben ser bases distintas.")
    with closing(_abrir_historial(historial)) as destino:
        destino.execute("BEGIN IMMEDIATE")
        try:
            HistorialSQLite._verificar(destino, permitir_creacion=False)
            with closing(_conectar_existente(base)) as conexion:
                conexion.execute("BEGIN IMMEDIATE")
                try:
                    _validar_base(conexion)
                    sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                    identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                    if identidad is None or identidad.rol not in {"administrador", "conductor"}:
                        raise SesionNoAutorizada()
                    usuario_id = identidad.usuario_id
                    with flota._bloqueo:
                        servicio = flota._servicios.get(usuario_id)
                        if (servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None)):
                            raise FlotaBloqueada("Hay una carrera en memoria que necesita revisión.")
                        if usuario_id in flota._cierres_preparados:
                            raise FlotaBloqueada("Hay un cierre en memoria que necesita revisión.")
                        if conexion.execute("SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario_id,)).fetchone():
                            raise FlotaBloqueada("Hay una intención sin resolver.")
                        if conexion.execute("""SELECT 1 FROM carreras_recuperacion WHERE usuario_id=?
                            AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')""",
                            (usuario_id,)).fetchone():
                            raise FlotaBloqueada("Hay una carrera sin resolver.")
                        filas = conexion.execute("""SELECT id FROM carreras_recuperacion
                            WHERE usuario_id=? AND estado_registro='guardada' AND revision=?""",
                            (usuario_id, revision_esperada + 1)).fetchall()
                        if len(filas) != 1 or revision_durable(conexion, usuario_id) != revision_esperada + 1:
                            raise OperacionNoPermitida("El cierre no coincide con la última revisión guardada.")
                        _, estado, resumen = _leer(conexion, usuario_id, filas[0]["id"])
                        fila_punto = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?", (filas[0]["id"], usuario_id)).fetchone()
                        if huella_esperada is not None and huella_revision(_desde_fila(fila_punto), revision_esperada) != huella_esperada:
                            raise OperacionNoPermitida("El punto ha cambiado; revísalo antes de confirmar.")
                        if estado != "guardada" or resumen.fin != ultimo_control_esperado:
                            raise OperacionNoPermitida("El cierre no coincide con el punto confirmado.")
                        _comprobar_fila(destino, _fila(resumen), usuario_id, permitir_insertar=False)
                        conexion.rollback()
                        destino.rollback()
                        flota._revisiones[usuario_id] = revision_esperada + 1
                        flota._servicios.pop(usuario_id, None)
                        flota._bloqueados.discard(usuario_id)
                        return resumen
                except BaseException:
                    conexion.rollback()
                    raise
        finally:
            destino.rollback()
