"""Resolución interna experimental de acciones interrumpidas.

El integrador debe mantener un bloqueo exclusivo de instancia. No abre rutas
web ni permite inferir lo que ocurrió físicamente fuera de la aplicación.
"""
from contextlib import closing
from datetime import datetime

from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.flota import FlotaEnMemoria, FlotaBloqueada, OperacionNoPermitida
from taxiroute.intenciones_operacion import _validar_base
from taxiroute.lectura_pendientes import huella_intencion, leer_estado_pendiente_en_transaccion
from taxiroute.puntos_control import _desde_fila
from taxiroute.revision_durable import revision_durable
from taxiroute.sesiones import SesionNoAutorizada, _hash_token, _identidad_vigente


def resolver_intencion_revisada(sesiones, token, flota, usuario_id, *, huella, confirmar):
    """Registra una revisión explícita sin ejecutar la acción incierta.

    Para iniciar sin punto exige administrador. Para mover o parar admite al
    conductor afectado o a un administrador y conserva el punto en revisión.
    El usuario debe haber recibido y revisado previamente el mismo diagnóstico.
    Una confirmación de commit incierta mantiene el bloqueo; el reintento se
    contrasta con el acta, la barrera y el estado persistente antes de liberar.
    """
    if (not isinstance(flota, FlotaEnMemoria) or not flota._modo_durable_experimental
            or not isinstance(usuario_id, str) or not usuario_id.strip() or usuario_id != usuario_id.strip()
            or confirmar is not True or not isinstance(huella, str) or len(huella) != 64
            or any(c not in "0123456789abcdef" for c in huella)):
        raise OperacionNoPermitida("La revisión o confirmación no es válida.")
    protegido = _hash_token(token)
    if protegido is None:
        raise SesionNoAutorizada()
    with closing(_conectar_existente(sesiones.ruta)) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        try:
            _validar_base(conexion)
            sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
            ahora = sesiones._reloj()
            identidad = _identidad_vigente(conexion, sesion, ahora)
            if (identidad is None or identidad.rol not in {"administrador", "conductor"}
                    or (identidad.rol != "administrador" and identidad.usuario_id != usuario_id)):
                raise SesionNoAutorizada()
            with flota._bloqueo:
                def validar_sesion():
                    instante = sesiones._reloj()
                    vigente = _identidad_vigente(conexion, sesion, instante)
                    if vigente != identidad:
                        raise SesionNoAutorizada()
                    return instante

                # Puede haber transcurrido tiempo mientras se esperaba el
                # bloqueo de flota, incluso con la base ya bloqueada.
                ahora = validar_sesion()
                servicio = flota._servicios.get(usuario_id)
                if (servicio is not None and (servicio.carrera is not None or servicio.pendiente is not None)) or usuario_id in flota._cierres_preparados:
                    raise FlotaBloqueada("Hay datos en memoria que requieren revisión o reinicio.")
                punto, intencion = leer_estado_pendiente_en_transaccion(conexion, usuario_id)
                if intencion is None:
                    actas = conexion.execute("SELECT * FROM intenciones_descartadas WHERE usuario_id=? AND huella=?", (usuario_id, huella)).fetchall()
                    if len(actas) != 1:
                        raise OperacionNoPermitida("No hay una revisión coincidente.")
                    acta = actas[0]
                    if acta["actor_id"] != identidad.usuario_id:
                        raise SesionNoAutorizada()
                    nueva_revision = acta["revision"] + 1
                    barrera = conexion.execute("SELECT revision FROM barrera_revision WHERE usuario_id=?", (usuario_id,)).fetchone()
                    if barrera is None or barrera["revision"] != nueva_revision or revision_durable(conexion, usuario_id) != nueva_revision:
                        raise OperacionNoPermitida("El estado cambió después de la revisión.")
                    if acta["accion"] == "iniciar":
                        if identidad.rol != "administrador" or punto is not None:
                            raise OperacionNoPermitida("La cuenta necesita otra revisión.")
                        resultado = "inicio_revisado"
                    else:
                        if (punto is None or punto.estado != "revision_pendiente"
                                or punto.punto.carrera_id != acta["carrera_id"]
                                or punto.punto.revision != acta["revision"]):
                            raise OperacionNoPermitida("La carrera cambió después de la revisión.")
                        resultado = "carrera_pendiente"
                    validar_sesion()
                    conexion.rollback()
                    flota._revisiones[usuario_id] = nueva_revision
                    if resultado == "inicio_revisado":
                        flota._bloqueados.discard(usuario_id)
                    else:
                        flota._bloqueados.add(usuario_id)
                    return resultado
                if usuario_id not in flota._bloqueados:
                    raise OperacionNoPermitida("La cuenta no está bloqueada para revisión.")
                if intencion.accion == "iniciar" and identidad.rol != "administrador":
                    raise SesionNoAutorizada()
                if huella_intencion(intencion, punto) != huella:
                    raise OperacionNoPermitida("El diagnóstico ha cambiado; revísalo de nuevo.")
                if (intencion.revision >= 2**63 - 1 or revision_durable(conexion, usuario_id) != intencion.revision
                        or conexion.execute("SELECT 1 FROM recibos_operacion WHERE id=?", (intencion.id,)).fetchone()):
                    raise OperacionNoPermitida("La intención no coincide con el estado disponible.")
                creada = datetime.fromisoformat(intencion.creada_en)
                if ahora.tzinfo is None or ahora.utcoffset() is None or ahora < creada:
                    raise OperacionNoPermitida("La fecha de revisión no es válida.")
                if punto is not None:
                    resultado = conexion.execute("""UPDATE carreras_recuperacion SET estado_registro='revision_pendiente'
                        WHERE id=? AND usuario_id=? AND estado_registro='activa' AND revision=?""",
                        (intencion.carrera_id, usuario_id, intencion.revision))
                    if resultado.rowcount != 1:
                        raise OperacionNoPermitida("La carrera cambió durante la revisión.")
                    guardada = conexion.execute("SELECT * FROM carreras_recuperacion WHERE id=? AND usuario_id=?",
                                                (intencion.carrera_id, usuario_id)).fetchone()
                    if guardada is None or guardada["estado_registro"] != "revision_pendiente" or _desde_fila(guardada) != punto.punto:
                        raise OperacionNoPermitida("No se pudo verificar la carrera en revisión.")
                valores = (intencion.id, usuario_id, identidad.usuario_id, intencion.accion,
                           intencion.revision, intencion.carrera_id, intencion.creada_en, ahora.isoformat(), huella)
                conexion.execute("""INSERT INTO intenciones_descartadas
                    (id, usuario_id, actor_id, accion, revision, carrera_id, creada_en, resuelta_en, huella)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", valores)
                acta = conexion.execute("SELECT id, usuario_id, actor_id, accion, revision, carrera_id, creada_en, resuelta_en, huella FROM intenciones_descartadas WHERE id=?", (intencion.id,)).fetchone()
                if acta is None or tuple(acta) != valores:
                    raise OperacionNoPermitida("No se pudo confirmar el registro de revisión.")
                nueva_revision = intencion.revision + 1
                conexion.execute("""INSERT INTO barrera_revision(usuario_id, revision) VALUES (?, ?)
                    ON CONFLICT(usuario_id) DO UPDATE SET revision=excluded.revision""", (usuario_id, nueva_revision))
                barrera = conexion.execute("SELECT revision FROM barrera_revision WHERE usuario_id=?", (usuario_id,)).fetchone()
                if barrera is None or barrera["revision"] != nueva_revision:
                    raise OperacionNoPermitida("No se pudo confirmar la revisión de la cuenta.")
                eliminada = conexion.execute("DELETE FROM intenciones_operacion WHERE id=? AND usuario_id=?", (intencion.id, usuario_id))
                if eliminada.rowcount != 1 or conexion.execute("SELECT 1 FROM intenciones_operacion WHERE id=?", (intencion.id,)).fetchone():
                    raise OperacionNoPermitida("No se pudo registrar la revisión.")
                validar_sesion()
                conexion.commit()
                flota._revisiones[usuario_id] = nueva_revision
                if intencion.accion == "iniciar":
                    flota._bloqueados.discard(usuario_id)
                    return "inicio_revisado"
                return "carrera_pendiente"
        except BaseException:
            conexion.rollback()
            raise
