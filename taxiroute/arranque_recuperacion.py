"""Arranque experimental con bloqueos cargados desde SQLite.

Solo crea la flota después de leer y validar los registros. No reconstruye ni
finaliza carreras; tampoco coordina otros procesos o escrituras posteriores.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from uuid import UUID

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.finalizacion_pendiente import ResumenPendiente, resumen_para_historial
from taxiroute.flota import FlotaEnMemoria
from taxiroute.puntos_control import PuntoControlInvalido, _desde_fila


class ArranqueRecuperacionError(Exception):
    """La base no permite crear una flota experimental segura."""


@dataclass(frozen=True)
class PendienteAlArrancar:
    usuario_id: str
    carrera_id: str
    estado: str


@dataclass(frozen=True)
class IntencionAlArrancar:
    id: str
    usuario_id: str
    accion: str
    revision: int
    carrera_id: str | None
    creada_en: str


@dataclass(frozen=True)
class FlotaInicializada:
    flota: FlotaEnMemoria
    pendientes: tuple[PendienteAlArrancar, ...]
    intenciones: tuple[IntencionAlArrancar, ...] = ()


def _validar_intencion(fila):
    """Valida el registro, sin inferir si la operación llegó a ejecutarse."""
    identificador = fila["id"]
    usuario_id = fila["usuario_id"]
    accion, revision, carrera_id = fila["accion"], fila["revision"], fila["carrera_id"]
    if (not isinstance(identificador, str) or str(UUID(identificador)) != identificador
            or not isinstance(usuario_id, str) or not usuario_id.strip()
            or usuario_id != usuario_id.strip() or fila["propietario_existente"] is None
            or accion not in {"iniciar", "mover", "parar"}
            or type(revision) is not int or not 0 <= revision <= 2**63 - 1):
        raise ArranqueRecuperacionError("Una intención tiene datos inválidos.")
    if accion == "iniciar":
        if carrera_id is not None:
            raise ArranqueRecuperacionError("Una intención de inicio es incoherente.")
    elif not isinstance(carrera_id, str) or not carrera_id.strip() or carrera_id != carrera_id.strip():
        raise ArranqueRecuperacionError("Una intención no identifica una carrera válida.")
    fecha = datetime.fromisoformat(fila["creada_en"])
    if fecha.tzinfo is None or fecha.utcoffset() is None:
        raise ArranqueRecuperacionError("Una intención no tiene fecha con zona horaria.")
    return IntencionAlArrancar(identificador, usuario_id, accion, revision, carrera_id, fila["creada_en"])


def crear_flota_con_recuperacion(ruta, crear_servicio, *, modo_durable_experimental=False) -> FlotaInicializada:
    """Valida la base existente en lectura; no autentica ni modifica sus datos."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ArranqueRecuperacionError("No se encuentra la base de recuperación.")
    try:
        uri = ruta.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)) as conexion:
            conexion.row_factory = sqlite3.Row
            conexion.execute("BEGIN")
            version = conexion.execute("PRAGMA user_version").fetchone()[0]
            identificador = conexion.execute("PRAGMA application_id").fetchone()[0]
            if version != VERSION_ESQUEMA or identificador != IDENTIFICADOR_APLICACION:
                raise ArranqueRecuperacionError("La versión o identidad de la base no es compatible.")
            filas = conexion.execute("""SELECT c.*, u.id AS propietario_existente
                FROM carreras_recuperacion AS c
                LEFT JOIN usuarios AS u ON u.id=c.usuario_id""").fetchall()
            pendientes = []
            usuarios_pendientes = set()
            for fila in filas:
                estado = fila["estado_registro"]
                if fila["propietario_existente"] is None or estado not in {
                        "activa", "revision_pendiente", "guardado_pendiente", "guardada"}:
                    raise ArranqueRecuperacionError("Una carrera tiene propietario o estado inválidos.")
                punto = _desde_fila(fila)
                if estado in {"activa", "revision_pendiente"}:
                    if fila["fin"] is not None or fila["importe_final"] is not None:
                        raise ArranqueRecuperacionError("Una carrera activa tiene datos finales incoherentes.")
                else:
                    fin = datetime.fromisoformat(fila["fin"])
                    importe = Decimal(fila["importe_final"])
                    resumen_para_historial(ResumenPendiente(punto, fin, importe))
                if estado != "guardada":
                    if punto.usuario_id in usuarios_pendientes:
                        raise ArranqueRecuperacionError("Una cuenta tiene varias carreras sin resolver.")
                    usuarios_pendientes.add(punto.usuario_id)
                    pendientes.append(PendienteAlArrancar(punto.usuario_id, punto.carrera_id, estado))
            filas_intenciones = conexion.execute("""SELECT i.*, u.id AS propietario_existente
                FROM intenciones_operacion AS i
                LEFT JOIN usuarios AS u ON u.id=i.usuario_id""").fetchall()
            intenciones = []
            usuarios_intenciones = set()
            for fila in filas_intenciones:
                intencion = _validar_intencion(fila)
                if intencion.usuario_id in usuarios_intenciones:
                    raise ArranqueRecuperacionError("Una cuenta tiene varias intenciones sin resolver.")
                usuarios_intenciones.add(intencion.usuario_id)
                intenciones.append(intencion)
            usuarios_pendientes.update(usuarios_intenciones)
            from taxiroute.revision_durable import revision_durable
            revisiones = {}
            for fila in conexion.execute("SELECT id FROM usuarios"):
                usuario_id = fila["id"]
                revisiones[usuario_id] = revision_durable(conexion, usuario_id)
            for intencion in intenciones:
                if (intencion.accion == "iniciar"
                        and intencion.revision != revisiones[intencion.usuario_id]):
                    raise ArranqueRecuperacionError("La revisión de inicio no coincide con la base.")
            conexion.rollback()
    except ArranqueRecuperacionError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, InvalidOperation) as error:
        raise ArranqueRecuperacionError("No se pueden validar los datos de recuperación.") from error
    flota = FlotaEnMemoria(crear_servicio, usuarios_pendientes=usuarios_pendientes,
                         revisiones_iniciales=revisiones,
                         modo_durable_experimental=modo_durable_experimental)
    return FlotaInicializada(flota, tuple(pendientes), tuple(intenciones))