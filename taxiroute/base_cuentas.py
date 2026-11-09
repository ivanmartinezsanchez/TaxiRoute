"""Conexiones e inicialización versionada del almacenamiento local de cuentas."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


VERSION_ESQUEMA = 10
# Marca propia para no confundir una base ajena con la de TaxiRoute.
IDENTIFICADOR_APLICACION = 0x54585254

CREAR_USUARIOS = """
CREATE TABLE usuarios (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
    nombre_usuario TEXT NOT NULL CHECK(length(trim(nombre_usuario)) > 0),
    nombre_usuario_normalizado TEXT NOT NULL UNIQUE
        CHECK(length(trim(nombre_usuario_normalizado)) > 0),
    nombre_visible TEXT NOT NULL CHECK(length(trim(nombre_visible)) > 0),
    rol TEXT NOT NULL CHECK(rol IN ('administrador', 'conductor')),
    hash_contrasena TEXT NOT NULL CHECK(length(hash_contrasena) > 0),
    habilitado INTEGER NOT NULL DEFAULT 1 CHECK(habilitado IN (0, 1)),
    bloqueado_en TEXT,
    intentos_fallidos INTEGER NOT NULL DEFAULT 0 CHECK(intentos_fallidos >= 0),
    proximo_intento_en TEXT,
    cambio_obligatorio INTEGER NOT NULL DEFAULT 1 CHECK(cambio_obligatorio IN (0, 1)),
    version_credenciales INTEGER NOT NULL DEFAULT 1 CHECK(version_credenciales >= 1),
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    ultimo_acceso_en TEXT
) STRICT
"""


MIGRACION_PIN = (
    """CREATE TABLE configuracion_administrador (
        usuario_id TEXT PRIMARY KEY NOT NULL REFERENCES usuarios(id),
        correo_verificacion TEXT NOT NULL CHECK(length(trim(correo_verificacion)) > 0)
    ) STRICT""",
    """CREATE TABLE desafios_acceso (
        id TEXT PRIMARY KEY NOT NULL,
        usuario_id TEXT NOT NULL REFERENCES usuarios(id),
        verificador_pin TEXT NOT NULL,
        version_credenciales INTEGER NOT NULL CHECK(version_credenciales >= 1),
        correo_destino TEXT NOT NULL,
        creado_en TEXT NOT NULL,
        caduca_en TEXT NOT NULL,
        consumido_en TEXT,
        invalidado_en TEXT,
        fallos INTEGER NOT NULL DEFAULT 0 CHECK(fallos >= 0),
        estado_envio TEXT NOT NULL CHECK(estado_envio IN ('pendiente', 'enviado', 'fallido'))
    ) STRICT""",
    "CREATE INDEX desafios_por_usuario_fecha ON desafios_acceso(usuario_id, creado_en)",
)


MIGRACION_AUTORIZACION = """CREATE TABLE autorizaciones_cambio (
    hash_token TEXT PRIMARY KEY NOT NULL,
    desafio_id TEXT NOT NULL UNIQUE REFERENCES desafios_acceso(id),
    creado_en TEXT NOT NULL,
    caduca_en TEXT NOT NULL,
    consumido_en TEXT
) STRICT"""


MIGRACION_SESIONES = (
    """CREATE TABLE sesiones (
        id TEXT PRIMARY KEY NOT NULL,
        hash_token TEXT NOT NULL UNIQUE,
        usuario_id TEXT NOT NULL REFERENCES usuarios(id),
        version_credenciales INTEGER NOT NULL,
        rol TEXT NOT NULL CHECK(rol IN ('administrador', 'conductor')),
        correo_verificacion TEXT,
        creado_en TEXT NOT NULL,
        ultima_interaccion_en TEXT NOT NULL,
        caduca_en TEXT NOT NULL,
        revocada_en TEXT
    ) STRICT""",
    "CREATE INDEX sesiones_por_usuario ON sesiones(usuario_id)",
)


MIGRACION_AUDITORIA = """CREATE TABLE auditoria_cuentas (
    id INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES usuarios(id),
    afectado_id TEXT NOT NULL REFERENCES usuarios(id),
    accion TEXT NOT NULL CHECK(accion IN ('desactivar', 'reactivar', 'desbloquear', 'revocar')),
    fecha TEXT NOT NULL
) STRICT"""


MIGRACION_RECUPERACION = (
    """CREATE TABLE carreras_recuperacion (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
        usuario_id TEXT NOT NULL REFERENCES usuarios(id),
        estado_registro TEXT NOT NULL CHECK(estado_registro IN
            ('activa', 'revision_pendiente', 'guardado_pendiente', 'guardada')),
        estado_vehiculo TEXT NOT NULL CHECK(estado_vehiculo IN ('parado', 'en movimiento')),
        inicio TEXT NOT NULL CHECK(length(trim(inicio)) > 0),
        ultimo_control TEXT NOT NULL CHECK(length(trim(ultimo_control)) > 0),
        fin TEXT,
        parado_ns INTEGER NOT NULL CHECK(parado_ns >= 0),
        movimiento_ns INTEGER NOT NULL CHECK(movimiento_ns >= 0),
        tarifa_parado TEXT NOT NULL CHECK(length(trim(tarifa_parado)) > 0),
        tarifa_movimiento TEXT NOT NULL CHECK(length(trim(tarifa_movimiento)) > 0),
        importe_final TEXT,
        revision INTEGER NOT NULL CHECK(revision >= 0),
        CHECK((estado_registro IN ('activa', 'revision_pendiente')
               AND fin IS NULL AND importe_final IS NULL)
           OR (estado_registro IN ('guardado_pendiente', 'guardada')
               AND fin IS NOT NULL AND importe_final IS NOT NULL))
    ) STRICT""",
    """CREATE UNIQUE INDEX una_carrera_pendiente_por_usuario
       ON carreras_recuperacion(usuario_id)
       WHERE estado_registro IN ('activa', 'revision_pendiente', 'guardado_pendiente')""",
    "CREATE INDEX carreras_recuperacion_por_usuario ON carreras_recuperacion(usuario_id)",
)

MIGRACION_AUDITORIA_V7 = (
    """CREATE TABLE auditoria_cuentas_v7 (
        id INTEGER PRIMARY KEY,
        actor_id TEXT NOT NULL REFERENCES usuarios(id),
        afectado_id TEXT NOT NULL REFERENCES usuarios(id),
        accion TEXT NOT NULL CHECK(accion IN ('desactivar', 'reactivar', 'desbloquear', 'revocar', 'alta')),
        fecha TEXT NOT NULL
    ) STRICT""",
    """INSERT INTO auditoria_cuentas_v7 (id, actor_id, afectado_id, accion, fecha)
       SELECT id, actor_id, afectado_id, accion, fecha FROM auditoria_cuentas""",
    "DROP TABLE auditoria_cuentas",
    "ALTER TABLE auditoria_cuentas_v7 RENAME TO auditoria_cuentas",
)

MIGRACION_AUDITORIA_V8 = (
    """CREATE TABLE auditoria_cuentas_v8 (
        id INTEGER PRIMARY KEY,
        actor_id TEXT NOT NULL REFERENCES usuarios(id),
        afectado_id TEXT NOT NULL REFERENCES usuarios(id),
        accion TEXT NOT NULL CHECK(accion IN ('desactivar', 'reactivar', 'desbloquear', 'revocar', 'alta', 'restablecer_contrasena')),
        fecha TEXT NOT NULL
    ) STRICT""",
    """INSERT INTO auditoria_cuentas_v8 (id, actor_id, afectado_id, accion, fecha)
       SELECT id, actor_id, afectado_id, accion, fecha FROM auditoria_cuentas""",
    "DROP TABLE auditoria_cuentas",
    "ALTER TABLE auditoria_cuentas_v8 RENAME TO auditoria_cuentas",
)

MIGRACION_INTENCIONES_V9 = (
    """CREATE TABLE intenciones_operacion (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
        usuario_id TEXT NOT NULL REFERENCES usuarios(id),
        accion TEXT NOT NULL CHECK(accion IN ('iniciar', 'mover', 'parar')),
        revision INTEGER NOT NULL CHECK(revision >= 0),
        carrera_id TEXT,
        creada_en TEXT NOT NULL CHECK(length(trim(creada_en)) > 0),
        CHECK((accion='iniciar' AND carrera_id IS NULL)
           OR (accion IN ('mover', 'parar') AND carrera_id IS NOT NULL
               AND length(trim(carrera_id)) > 0))
    ) STRICT""",
    "CREATE UNIQUE INDEX una_intencion_por_usuario ON intenciones_operacion(usuario_id)",
)

MIGRACION_RECIBOS_V10 = (
    """CREATE TABLE recibos_operacion (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
        usuario_id TEXT NOT NULL REFERENCES usuarios(id),
        accion TEXT NOT NULL CHECK(accion IN ('iniciar', 'mover', 'parar')),
        revision INTEGER NOT NULL CHECK(revision >= 0),
        punto_json TEXT NOT NULL CHECK(length(trim(punto_json)) > 0),
        creado_en TEXT NOT NULL CHECK(length(trim(creado_en)) > 0)
    ) STRICT""",
    "CREATE INDEX recibos_por_usuario ON recibos_operacion(usuario_id)",
)

class EsquemaCuentasError(RuntimeError):
    """La base no puede utilizarse con esta versión del programa."""


@contextmanager
def conectar(ruta: str | Path):
    """Abre una conexión independiente y la cierra incluso si ocurre un error.

    Las escrituras requieren una transacción explícita. No crea directorios,
    no inicializa tablas y no comparte conexiones entre peticiones.
    """
    conexion = sqlite3.connect(str(ruta), timeout=5, isolation_level=None)
    try:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        yield conexion
    finally:
        conexion.close()


def inicializar_base(ruta: str | Path) -> None:
    """Crea o actualiza el esquema sin borrar los datos existentes.

    La carpeta debe existir. El llamador decide dónde almacenar los datos.
    No crea cuentas, contraseñas ni sesiones.
    """
    with conectar(ruta) as conexion:
        # Serializa inicializaciones concurrentes antes de consultar la versión.
        conexion.execute("BEGIN IMMEDIATE")
        try:
            version = conexion.execute("PRAGMA user_version").fetchone()[0]
            identificador = conexion.execute("PRAGMA application_id").fetchone()[0]
            if version > VERSION_ESQUEMA:
                raise EsquemaCuentasError("La base requiere una versión más reciente de TaxiRoute.")
            if identificador not in (0, IDENTIFICADOR_APLICACION):
                raise EsquemaCuentasError("La base pertenece a otra aplicación.")
            if version == 0:
                objetos = conexion.execute(
                    "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if objetos or identificador != 0:
                    raise EsquemaCuentasError("La base sin versión no está vacía; no se modificará.")
                conexion.execute(CREAR_USUARIOS)
                conexion.execute(f"PRAGMA application_id = {IDENTIFICADOR_APLICACION}")
                version = 1
            elif identificador != IDENTIFICADOR_APLICACION:
                raise EsquemaCuentasError("La base no está identificada como TaxiRoute.")
            elif conexion.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'usuarios'"
            ).fetchone() is None:
                raise EsquemaCuentasError("Falta la tabla de usuarios; la base necesita revisión.")
            if version == 1:
                for sentencia in MIGRACION_PIN:
                    conexion.execute(sentencia)
                version = 2
            if version == 2:
                conexion.execute(MIGRACION_AUTORIZACION)
                version = 3
            if version == 3:
                for sentencia in MIGRACION_SESIONES:
                    conexion.execute(sentencia)
                version = 4
            if version == 4:
                conexion.execute(MIGRACION_AUDITORIA)
                version = 5
            if version == 5:
                for sentencia in MIGRACION_RECUPERACION:
                    conexion.execute(sentencia)
                version = 6
            if version == 6:
                # No eliminar silenciosamente extensiones o auditorías propias.
                objetos = conexion.execute("""SELECT name FROM sqlite_schema
                    WHERE tbl_name='auditoria_cuentas' AND type IN ('index', 'trigger')
                    AND name NOT LIKE 'sqlite_%'""").fetchall()
                if objetos:
                    raise EsquemaCuentasError("La auditoría tiene extensiones que requieren revisión antes de migrar.")
                for sentencia in MIGRACION_AUDITORIA_V7:
                    conexion.execute(sentencia)
                version = 7
            if version == 7:
                objetos = conexion.execute("""SELECT name FROM sqlite_schema
                    WHERE tbl_name='auditoria_cuentas' AND type IN ('index', 'trigger')
                    AND name NOT LIKE 'sqlite_%'""").fetchall()
                if objetos:
                    raise EsquemaCuentasError("La auditoría tiene extensiones que requieren revisión antes de migrar.")
                for sentencia in MIGRACION_AUDITORIA_V8:
                    conexion.execute(sentencia)
                version = 8
            if version == 8:
                for sentencia in MIGRACION_INTENCIONES_V9:
                    conexion.execute(sentencia)
                version = 9
            if version == 9:
                for sentencia in MIGRACION_RECIBOS_V10:
                    conexion.execute(sentencia)
                version = 10
                conexion.execute(f"PRAGMA user_version = {version}")
            for tabla in ("usuarios", "configuracion_administrador", "desafios_acceso",
                          "autorizaciones_cambio", "sesiones", "auditoria_cuentas",
                          "carreras_recuperacion", "intenciones_operacion", "recibos_operacion"):
                if conexion.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (tabla,)
                ).fetchone() is None:
                    raise EsquemaCuentasError("Falta una tabla de cuentas; la base necesita revisión.")
            conexion.commit()
        except BaseException:
            conexion.rollback()
            raise
