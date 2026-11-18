"""Organización experimental de servicios de carreras por usuario.

Este módulo no autentica: quien lo integre debe obtener el identificador de una
sesión verificada. El estado vive en memoria y se pierde al detener el proceso.
"""
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from typing import Callable

from taxiroute.carrera import CapturaCarrera
from taxiroute.servicio import ServicioCarreras


class FlotaBloqueada(Exception):
    """La carrera necesita reconciliación antes de admitir nuevas acciones."""


class OperacionDurableRequerida(FlotaBloqueada):
    """El modo experimental impide ejecutar una operación sin coordinación."""


class OperacionNoPermitida(Exception):
    """La operación no corresponde al estado actual de la carrera."""


class RevisionDesactualizada(Exception):
    """La acción se basó en un estado anterior del mismo usuario."""


@dataclass(frozen=True)
class ResultadoFlota:
    """Copia inmutable de la operación y del estado de una carrera."""
    mensaje: str
    activa: bool
    estado: str
    importe: Decimal | None
    pendiente: bool
    revision: int
    motivo_guardado: str | None = None


@dataclass(frozen=True)
class CapturaFlota:
    """Captura y revisión observadas durante la misma sección protegida."""
    carrera: CapturaCarrera
    revision: int

class FlotaEnMemoria:
    """Un servicio por usuario, serializado dentro de este proceso.

    Es un prototipo: no persiste carreras, no administra vehículos ni comprueba
    permisos. La fábrica debe construir un ServicioCarreras independiente.
    """
    _COMANDOS = frozenset({"iniciar", "mover", "parar", "finalizar", "reintentar"})

    def __init__(self, crear_servicio: Callable[[str], ServicioCarreras], *,
                 usuarios_pendientes=(), modo_durable_experimental=False, revisiones_iniciales=None):
        if type(modo_durable_experimental) is not bool:
            raise ValueError("El modo durable debe ser verdadero o falso.")
        self._modo_durable_experimental = modo_durable_experimental
        self._crear_servicio = crear_servicio
        self._servicios: dict[str, ServicioCarreras] = {}
        revisiones = {} if revisiones_iniciales is None else dict(revisiones_iniciales)
        if any(not isinstance(usuario, str) or not usuario.strip() or usuario != usuario.strip()
               or type(revision) is not int or not 0 <= revision <= 2**63 - 1
               for usuario, revision in revisiones.items()):
            raise ValueError("Revisiones iniciales no válidas.")
        self._revisiones: dict[str, int] = revisiones
        if isinstance(usuarios_pendientes, str):
            raise ValueError("Se necesita una colección de identificadores.")
        pendientes = set(usuarios_pendientes)
        if any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in pendientes):
            raise ValueError("Identificador de cuenta pendiente no válido.")
        self._bloqueados: set[str] = pendientes
        self._cierres_preparados = {}
        self._bloqueo = RLock()

    def _exigir_via_coordinada(self):
        if self._modo_durable_experimental:
            raise OperacionDurableRequerida("La operación necesita la vía durable coordinada.")

    def ejecutar(self, usuario_id: str, comando: str) -> ResultadoFlota:
        """Ejecuta una operación sobre el servicio del identificador recibido."""
        self._exigir_via_coordinada()
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        if not isinstance(comando, str) or comando not in self._COMANDOS:
            raise ValueError("Operación de flota no admitida.")
        with self._bloqueo:
            if usuario_id in self._bloqueados:
                raise FlotaBloqueada("La carrera necesita reconciliación.")
            servicio = self._servicios.get(usuario_id)
            if servicio is None:
                servicio = self._crear_servicio(usuario_id)
                if not isinstance(servicio, ServicioCarreras):
                    raise TypeError("La fábrica debe devolver un ServicioCarreras.")
                if any(existente is servicio for existente in self._servicios.values()):
                    raise ValueError("Cada usuario necesita un servicio independiente.")
                self._servicios[usuario_id] = servicio
            mensaje, _ = servicio.ejecutar(comando)
            self._revisiones[usuario_id] = self._revisiones.get(usuario_id, 0) + 1
            return self._copiar_estado(servicio, mensaje, self._revisiones[usuario_id])

    def ejecutar_si_revision(self, usuario_id: str, comando: str, revision: int) -> ResultadoFlota:
        """Comprueba y ejecuta bajo el mismo bloqueo, sin alterar otra revisión."""
        self._exigir_via_coordinada()
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        if type(revision) is not int or revision < 0:
            raise ValueError("La revisión debe ser un entero no negativo.")
        with self._bloqueo:
            if self._revisiones.get(usuario_id, 0) != revision:
                raise RevisionDesactualizada("El estado ha cambiado. Actualiza la pantalla.")
            return self.ejecutar(usuario_id, comando)

    def ejecutar_controlado(self, usuario_id: str, comando: str, revision: int) -> ResultadoFlota:
        """Rechaza transiciones incompatibles antes de ejecutar una acción API."""
        self._exigir_via_coordinada()
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        if type(revision) is not int or revision < 0:
            raise ValueError("La revisión debe ser un entero no negativo.")
        if comando not in {"iniciar", "mover", "parar", "finalizar", "reintentar"}:
            raise ValueError("Operación no admitida.")
        with self._bloqueo:
            if usuario_id in self._bloqueados:
                raise FlotaBloqueada("La carrera necesita reconciliación.")
            if self._revisiones.get(usuario_id, 0) != revision:
                raise RevisionDesactualizada("El estado ha cambiado. Actualiza la pantalla.")
            servicio = self._servicios.get(usuario_id)
            carrera = servicio.carrera if servicio else None
            pendiente = servicio.pendiente if servicio else None
            if comando == "iniciar" and (carrera is not None or pendiente is not None):
                raise OperacionNoPermitida("Ya hay una carrera activa o pendiente de guardar.")
            if comando in {"mover", "parar", "finalizar"} and carrera is None:
                raise OperacionNoPermitida("No hay ninguna carrera activa.")
            if comando == "mover" and carrera.estado.value == "en movimiento":
                raise OperacionNoPermitida("El vehículo ya está en movimiento.")
            if comando == "parar" and carrera.estado.value == "parado":
                raise OperacionNoPermitida("El vehículo ya está parado.")
            if comando == "reintentar" and pendiente is None:
                raise OperacionNoPermitida("No hay ninguna carrera pendiente de guardar.")
            return self.ejecutar(usuario_id, comando)
    def consultar(self, usuario_id: str) -> ResultadoFlota | None:
        """Devuelve una copia del estado, sin crear servicios nuevos."""
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        with self._bloqueo:
            if usuario_id in self._bloqueados:
                raise FlotaBloqueada("La carrera necesita reconciliación.")
            servicio = self._servicios.get(usuario_id)
            return None if servicio is None else self._copiar_estado(servicio, "", self._revisiones[usuario_id])

    def capturar(self, usuario_id: str) -> CapturaFlota | None:
        """Copia carrera y revisión bajo el mismo bloqueo que las acciones."""
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        with self._bloqueo:
            if usuario_id in self._bloqueados:
                raise FlotaBloqueada("La carrera necesita reconciliación.")
            servicio = self._servicios.get(usuario_id)
            if servicio is None or servicio.carrera is None:
                return None
            return CapturaFlota(servicio.carrera.capturar(), self._revisiones[usuario_id])
    def guardar_captura(self, ruta, usuario_id: str):
        """Guarda un punto experimental: primero SQLite, después bloqueo de flota.

        No autentica ni debe llamarse desde una transacción SQLite ya abierta.
        La finalización durable y el guardado periódico siguen pendientes.
        """
        self._exigir_via_coordinada()
        from taxiroute.base_cuentas import conectar
        from taxiroute.puntos_control import _guardar_punto_en_transaccion, punto_de_captura

        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        with conectar(ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                with self._bloqueo:
                    if usuario_id in self._bloqueados:
                        raise FlotaBloqueada("La carrera necesita reconciliación.")
                    servicio = self._servicios.get(usuario_id)
                    if servicio is None or servicio.carrera is None:
                        conexion.commit()
                        return None
                    captura = servicio.carrera.capturar()
                    punto = punto_de_captura(captura, usuario_id, self._revisiones[usuario_id])
                    _guardar_punto_en_transaccion(conexion, punto)
                    conexion.commit()
                    return punto
            except BaseException:
                conexion.rollback()
                raise
    def requiere_reconciliacion(self, usuario_id: str) -> bool:
        """Indica un bloqueo de seguridad únicamente en esta instancia."""
        if not isinstance(usuario_id, str) or not usuario_id.strip():
            raise ValueError("Se necesita un identificador de usuario válido.")
        with self._bloqueo:
            return usuario_id in self._bloqueados

    def aplicar_intencion_experimental(self, sesiones, token, id_intencion):
        """Aplica una intención con punto y recibo durables en una transacción.

        Prototipo aislado: no conectar a web hasta coordinar todos los accesos.
        Si el commit falla, mantiene el bloqueo incluso al consultar el recibo.
        """
        from taxiroute.base_cuentas import conectar
        from taxiroute.intenciones_operacion import _validar_base
        from taxiroute.puntos_control import _desde_fila, _guardar_punto_en_transaccion, punto_de_captura
        from taxiroute.recibos_operacion import _recibo, _validar_id, guardar_recibo_en_transaccion
        from taxiroute.sesiones import _hash_token, _identidad_vigente, SesionNoAutorizada

        _validar_id(id_intencion)
        protegido = _hash_token(token)
        if protegido is None:
            raise SesionNoAutorizada()
        with conectar(sesiones.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                _validar_base(conexion)
                sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                if identidad is None or identidad.rol not in {"conductor", "administrador"}:
                    raise SesionNoAutorizada()
                intencion = conexion.execute(
                    "SELECT * FROM intenciones_operacion WHERE id=? AND usuario_id=?",
                    (id_intencion, identidad.usuario_id),
                ).fetchone()
                fila_recibo = conexion.execute(
                    "SELECT * FROM recibos_operacion WHERE id=? AND usuario_id=?",
                    (id_intencion, identidad.usuario_id),
                ).fetchone()
                if fila_recibo is not None:
                    if intencion is not None:
                        raise OperacionNoPermitida("La operación contiene datos contradictorios.")
                    punto_confirmado = _recibo(fila_recibo).punto
                    conexion.rollback()
                    return punto_confirmado
                if intencion is None:
                    raise OperacionNoPermitida("No se encuentra la intención propia.")
                usuario_id = identidad.usuario_id
                with self._bloqueo:
                    if usuario_id in self._bloqueados:
                        raise FlotaBloqueada("La operación requiere revisión.")
                    revision = self._revisiones.get(usuario_id, 0)
                    if revision != intencion["revision"]:
                        raise RevisionDesactualizada("La revisión de la intención no coincide.")
                    comando = intencion["accion"]
                    if comando not in {"iniciar", "mover", "parar"}:
                        raise OperacionNoPermitida("La intención no se puede aplicar.")
                    servicio = self._servicios.get(usuario_id)
                    carrera = servicio.carrera if servicio else None
                    punto_anterior = conexion.execute(
                        "SELECT * FROM carreras_recuperacion WHERE usuario_id=? "
                        "AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')",
                        (usuario_id,),
                    ).fetchone()
                    if comando == "iniciar":
                        if carrera is not None or (servicio and servicio.pendiente is not None) or punto_anterior is not None:
                            raise OperacionNoPermitida("Ya existe una carrera activa o pendiente.")
                        from taxiroute.revision_durable import revision_durable
                        if (intencion["carrera_id"] is not None
                                or revision_durable(conexion, usuario_id) != revision):
                            raise OperacionNoPermitida("La intención de inicio no es válida.")
                        if servicio is None:
                            servicio = self._crear_servicio(usuario_id)
                            if not isinstance(servicio, ServicioCarreras):
                                raise TypeError("La fábrica debe devolver un ServicioCarreras.")
                            if any(existente is servicio for existente in self._servicios.values()):
                                raise ValueError("Cada usuario necesita un servicio independiente.")
                            self._servicios[usuario_id] = servicio
                    else:
                        if carrera is None or punto_anterior is None or punto_anterior["estado_registro"] != "activa":
                            raise OperacionNoPermitida("No hay una carrera activa disponible.")
                        punto = _desde_fila(punto_anterior)
                        captura = carrera.capturar()
                        esperado = "parado" if comando == "mover" else "en movimiento"
                        if (punto.carrera_id != intencion["carrera_id"]
                                or captura.identificador != punto.carrera_id
                                or punto.revision != revision or punto.estado_vehiculo != esperado
                                or carrera.estado.value != esperado):
                            raise RevisionDesactualizada("La carrera no coincide con la intención.")
                    # Antes de invocar código que puede mutar, impedir toda otra acción
                    # en esta flota incluso si se produce una excepción inesperada.
                    self._bloqueados.add(usuario_id)
                    servicio.ejecutar(comando)
                    self._revisiones[usuario_id] = revision + 1
                    if servicio.carrera is None:
                        raise OperacionNoPermitida("La operación no produjo una carrera activa.")
                    punto = punto_de_captura(servicio.carrera.capturar(), usuario_id, revision + 1)
                    _guardar_punto_en_transaccion(conexion, punto)
                    guardar_recibo_en_transaccion(
                        conexion, id_intencion=id_intencion, usuario_id=usuario_id,
                        accion=comando, revision=revision, punto=punto, creado_en=sesiones._reloj())
                    eliminado = conexion.execute(
                        "DELETE FROM intenciones_operacion WHERE id=? AND usuario_id=?",
                        (id_intencion, usuario_id))
                    if eliminado.rowcount != 1 or conexion.execute(
                        "SELECT 1 FROM intenciones_operacion WHERE id=?", (id_intencion,)
                    ).fetchone() is not None:
                        raise OperacionNoPermitida("No se pudo confirmar la intención.")
                    conexion.commit()
                    self._bloqueados.discard(usuario_id)
                    return punto
            except BaseException:
                conexion.rollback()
                raise

    def preparar_cierre_durable_experimental(self, sesiones, token, revision: int):
        """Prepara un cierre durable sin finalizar memoria ni escribir historial.

        Retiene el bloqueo ante un commit incierto. Solo una conciliación futura
        podrá verificar el historial y retirar el servicio vivo. No es API web.
        """
        from taxiroute.base_cuentas import conectar
        from taxiroute.finalizacion_pendiente import preparar_punto_final_en_transaccion
        from taxiroute.intenciones_operacion import _validar_base
        from taxiroute.puntos_control import _desde_fila, punto_de_captura
        from taxiroute.sesiones import _hash_token, _identidad_vigente, SesionNoAutorizada

        if not self._modo_durable_experimental:
            raise OperacionNoPermitida("El cierre requiere el modo durable experimental.")
        if type(revision) is not int or not 0 <= revision < 2**63 - 1:
            raise ValueError("La revisión no es válida para preparar un cierre.")
        protegido = _hash_token(token)
        if protegido is None:
            raise SesionNoAutorizada()
        with conectar(sesiones.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                _validar_base(conexion)
                sesion = conexion.execute(
                    "SELECT * FROM sesiones WHERE hash_token=?", (protegido,)
                ).fetchone()
                identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                if identidad is None or identidad.rol not in {"conductor", "administrador"}:
                    raise SesionNoAutorizada()
                usuario_id = identidad.usuario_id
                with self._bloqueo:
                    if usuario_id in self._bloqueados:
                        raise FlotaBloqueada("La carrera necesita revisión.")
                    if self._revisiones.get(usuario_id, 0) != revision:
                        raise RevisionDesactualizada("El estado ha cambiado.")
                    if conexion.execute(
                        "SELECT 1 FROM intenciones_operacion WHERE usuario_id=?", (usuario_id,)
                    ).fetchone():
                        raise OperacionNoPermitida("Hay una intención pendiente.")
                    servicio = self._servicios.get(usuario_id)
                    if servicio is None or servicio.carrera is None or servicio.pendiente is not None:
                        raise OperacionNoPermitida("No hay una carrera activa disponible.")
                    filas = conexion.execute(
                        "SELECT * FROM carreras_recuperacion WHERE usuario_id=? "
                        "AND estado_registro IN ('activa','revision_pendiente','guardado_pendiente')",
                        (usuario_id,),
                    ).fetchall()
                    if len(filas) != 1 or filas[0]["estado_registro"] != "activa":
                        raise OperacionNoPermitida("No hay un punto activo verificable.")
                    anterior = _desde_fila(filas[0])
                    captura = servicio.carrera.capturar()
                    if (anterior.carrera_id != captura.identificador or anterior.revision != revision
                            or anterior.estado_vehiculo != captura.estado.value):
                        raise RevisionDesactualizada("El punto no coincide con la carrera activa.")
                    punto = punto_de_captura(captura, usuario_id, revision + 1)
                    resumen = preparar_punto_final_en_transaccion(conexion, punto, punto.ultimo_control)
                    # Tras un error de commit no se sabe si llegó a confirmarse.
                    self._bloqueados.add(usuario_id)
                    self._cierres_preparados[usuario_id] = resumen
                    conexion.commit()
                    return resumen
            except BaseException:
                conexion.rollback()
                raise

    def preparar_cierre_experimental(self, sesiones, token, revision: int):
        """Captura y prepara el cierre con sesión, sin finalizar memoria ni CSV.

        Mantiene SQLite -> flota hasta finalizar el commit. Tras confirmación o
        fallo incierto de commit, bloquea acciones hasta futura reconciliación.
        Es un prototipo no conectado a web y el bloqueo no sobrevive reinicios.
        """
        self._exigir_via_coordinada()
        from taxiroute.base_cuentas import conectar
        from taxiroute.finalizacion_pendiente import preparar_punto_final_en_transaccion
        from taxiroute.puntos_control import punto_de_captura
        from taxiroute.sesiones import _hash_token, _identidad_vigente, SesionNoAutorizada

        if type(revision) is not int or revision < 0:
            raise ValueError("La revisión debe ser un entero no negativo.")
        protegido = _hash_token(token)
        if protegido is None:
            raise SesionNoAutorizada()
        with conectar(sesiones.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                sesion = conexion.execute("SELECT * FROM sesiones WHERE hash_token=?", (protegido,)).fetchone()
                identidad = _identidad_vigente(conexion, sesion, sesiones._reloj())
                if identidad is None or identidad.rol not in {"conductor", "administrador"}:
                    raise SesionNoAutorizada()
                usuario_id = identidad.usuario_id
                with self._bloqueo:
                    if usuario_id in self._bloqueados:
                        raise FlotaBloqueada("La carrera necesita reconciliación.")
                    if self._revisiones.get(usuario_id, 0) != revision:
                        raise RevisionDesactualizada("El estado ha cambiado. Actualiza la pantalla.")
                    servicio = self._servicios.get(usuario_id)
                    if servicio is None or servicio.carrera is None:
                        raise ValueError("No hay ninguna carrera activa.")
                    captura = servicio.carrera.capturar()
                    punto = punto_de_captura(captura, usuario_id, revision + 1)
                    resumen = preparar_punto_final_en_transaccion(conexion, punto, punto.ultimo_control)
                    # El commit puede fallar con resultado incierto. Mantener el
                    # bloqueo también en ese caso; no prometer rollback externo.
                    self._bloqueados.add(usuario_id)
                    conexion.commit()
                    return resumen
            except BaseException:
                conexion.rollback()
                raise
    @staticmethod
    def _copiar_estado(servicio: ServicioCarreras, mensaje: str, revision: int) -> ResultadoFlota:
        carrera = servicio.carrera
        return ResultadoFlota(
            mensaje=mensaje,
            activa=carrera is not None,
            estado=carrera.estado.value if carrera is not None else "sin carrera activa",
            importe=carrera.importe_actual() if carrera is not None else (
                servicio.ultimo_resumen.importe if servicio.ultimo_resumen else None),
            pendiente=servicio.pendiente is not None,
            revision=revision,
            motivo_guardado=servicio.motivo_guardado,
        )
