"""Migración interna v14→v15 para ensayos aislados.

No está integrada en el lanzador ni autoriza actualizar instalaciones reales.
La imagen anterior rechaza v15; actualizar base e imagen requiere coordinación.
"""
import sqlite3
from uuid import uuid4

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION
from taxiroute.recibo_cierre_experimental import TABLA


class MigracionCierreError(ValueError):
    pass


_TABLAS_V14 = frozenset((
    "usuarios", "configuracion_administrador", "desafios_acceso", "autorizaciones_cambio",
    "sesiones", "auditoria_cuentas", "carreras_recuperacion", "intenciones_operacion",
    "recibos_operacion", "codigos_recuperacion", "vinculo_historial", "intenciones_descartadas",
    "barrera_revision", "revisiones_csv_ausente", "procedencia_sqlite",
))


def _esquema_recibos_valido(conexion):
    fila = conexion.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='recibos_cierre'").fetchone()
    if fila is None or " ".join(fila[0].split()) != " ".join(TABLA.split()):
        return False
    return not bool(conexion.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='recibos_cierre'").fetchone())


def migrar_en_transaccion(conexion):
    """Actualiza una base v14 en la transacción del llamador, sin confirmar.

    El llamador mantiene aislamiento operativo y una transacción de escritura;
    este módulo no comprueba contenedores, ACL, historial ni seguridad de copias.
    Repetir sobre una v15 con el esquema esperado no modifica datos.
    """
    if not conexion.in_transaction:
        raise MigracionCierreError("Se necesita una transacción de escritura activa.")
    if conexion.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise MigracionCierreError("La comprobación de claves externas debe estar activa.")
    version = conexion.execute("PRAGMA user_version").fetchone()[0]
    if conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION:
        raise MigracionCierreError("La base no pertenece a TaxiRoute.")
    presentes = {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if not _TABLAS_V14.issubset(presentes):
        raise MigracionCierreError("Faltan tablas necesarias; requiere revisión.")
    if [fila[0] for fila in conexion.execute("PRAGMA integrity_check")] != ["ok"]:
        raise MigracionCierreError("La integridad de la base requiere revisión.")
    if conexion.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigracionCierreError("Hay referencias inválidas; requiere revisión.")
    if version == 15:
        if not _esquema_recibos_valido(conexion):
            raise MigracionCierreError("El esquema de comprobantes requiere revisión.")
        return False
    if version != 14 or "recibos_cierre" in presentes:
        raise MigracionCierreError("La versión o tabla existente requiere revisión.")
    marca = "migracion_" + uuid4().hex
    conexion.execute(f"SAVEPOINT {marca}")
    try:
        conexion.execute(TABLA)
        conexion.execute("PRAGMA user_version = 15")
        if conexion.execute("PRAGMA user_version").fetchone()[0] != 15 or not _esquema_recibos_valido(conexion):
            raise MigracionCierreError("No se pudo confirmar el esquema nuevo.")
        conexion.execute(f"RELEASE SAVEPOINT {marca}")
        return True
    except BaseException:
        if conexion.in_transaction:
            conexion.execute(f"ROLLBACK TO SAVEPOINT {marca}")
            conexion.execute(f"RELEASE SAVEPOINT {marca}")
        raise
