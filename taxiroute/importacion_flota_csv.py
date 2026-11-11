"""Importación experimental de historiales vinculados CSV a SQLite.

No cambia el vínculo ni activa SQLite. Requiere detener las instancias que
respetan el bloqueo cooperativo y usar un destino ya inicializado. No modifica
los CSV; los historiales sin propietario quedan fuera de esta importación.
"""
from contextlib import ExitStack, closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import _conectar_existente, _leer
from taxiroute.historial import CAMPOS, _fila
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.identidad_historial_sqlite import _preparar_en_transaccion, _leer as _leer_identidad
from taxiroute.migracion_historial import _validar_fila
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.vinculo_historial import verificar_vinculo_csv, _registro


class ImportacionFlotaError(ValueError):
    """Los datos o el estado de la instalación requieren revisión."""


@dataclass(frozen=True)
class ResultadoImportacionFlota:
    cuentas: int
    filas_origen: int
    insertadas: int
    ya_existentes: int
    legado_sin_propietario: bool
    cuentas_sin_historial: int
    filas_sin_comprobante: int
    identidad_sqlite: str | None = None


def _comprobar_entradas(carpeta, usuarios):
    """Rechaza carpetas o CSV que no se puedan asociar a una cuenta."""
    for entrada in carpeta.iterdir():
        if entrada.is_symlink():
            raise ImportacionFlotaError("Hay un enlace no permitido en los historiales.")
        if entrada.is_dir():
            if entrada.name not in usuarios:
                raise ImportacionFlotaError("Hay un directorio sin cuenta conocida.")
            for hijo in entrada.iterdir():
                if hijo.is_symlink() or hijo.is_dir():
                    raise ImportacionFlotaError("Hay un enlace o subdirectorio no permitido.")
                if hijo.suffix.lower() == '.csv' and hijo.name != 'historial.csv':
                    raise ImportacionFlotaError("Hay un CSV no identificado en una cuenta.")
        elif entrada.suffix.lower() == '.csv' and entrada.name != 'historial.csv':
            raise ImportacionFlotaError("Hay un CSV sin propietario conocido.")


def _importar_flota_csv(ruta_cuentas, carpeta_historial, ruta_destino, *, activar):
    """Copia filas verificadas a SQLite, conservando cada usuario de origen.

    Las cuentas y el historial CSV deben estar vinculados; el destino SQLite
    debe existir e inicializarse explícitamente. La lista de carreras en el
    destino debe ser un subconjunto idéntico del origen, sin filas huérfanas.
    Los dos SQLite son archivos distintos. Si activar es verdadero se cambia
    el vínculo solo después de confirmar la identidad y las filas en SQLite.
    No habilita por sí sola la composición relacional ni hace un cambio atómico
    entre las dos bases. Los bloqueos solo coordinan procesos que los respetan.
    """
    base, carpeta, destino = map(Path, (ruta_cuentas, carpeta_historial, ruta_destino))
    if (base.is_symlink() or not base.is_file() or carpeta.is_symlink() or not carpeta.is_dir()
            or destino.is_symlink() or not destino.is_file()):
        raise ImportacionFlotaError("Se necesitan cuentas, carpeta y destino existentes sin enlaces.")
    base, carpeta, destino = base.resolve(), carpeta.resolve(), destino.resolve()
    if (os.path.samefile(base, destino) or destino.is_relative_to(carpeta)
            or (base.parent / 'servidor.lock').is_symlink()):
        raise ImportacionFlotaError("El destino debe ser independiente de las cuentas y los historiales CSV.")
    with bloqueo_historial(base.parent / 'servidor', espera=0):
        if not verificar_vinculo_csv(base, carpeta):
            raise ImportacionFlotaError("La instalación CSV no está vinculada.")
        with closing(_conectar_existente(base)) as cuentas:
            usuarios = tuple(f[0] for f in cuentas.execute('SELECT id FROM usuarios ORDER BY id'))
        _comprobar_entradas(carpeta, set(usuarios))
        historiales = {usuario: _historial_de_cuenta(carpeta, usuario) for usuario in usuarios}
        with ExitStack() as pila:
            for usuario in usuarios:
                pila.enter_context(bloqueo_historial(historiales[usuario].ruta))
            with closing(_conectar_existente(base)) as cuentas:
                cuentas.execute('BEGIN IMMEDIATE')
                try:
                    if tuple(f[0] for f in cuentas.execute('SELECT id FROM usuarios ORDER BY id')) != usuarios:
                        raise ImportacionFlotaError("El conjunto de cuentas ha cambiado.")
                    if (cuentas.execute("PRAGMA foreign_key_check").fetchone()
                            or cuentas.execute("SELECT 1 FROM carreras_recuperacion c LEFT JOIN usuarios u ON u.id=c.usuario_id WHERE u.id IS NULL LIMIT 1").fetchone()):
                        raise ImportacionFlotaError("La base contiene referencias de cuenta inválidas.")
                    vinculo = _registro(cuentas)
                    if vinculo is None or vinculo['formato'] != 'csv' or vinculo['estado'] != 'listo':
                        raise ImportacionFlotaError("El vínculo CSV no está disponible.")
                    if not verificar_vinculo_csv(base, carpeta):
                        raise ImportacionFlotaError("El vínculo CSV ha cambiado.")
                    if (cuentas.execute('SELECT 1 FROM intenciones_operacion LIMIT 1').fetchone()
                            or cuentas.execute("SELECT 1 FROM carreras_recuperacion WHERE estado_registro IN ('activa','revision_pendiente','guardado_pendiente') LIMIT 1").fetchone()):
                        raise ImportacionFlotaError("Hay carreras o acciones pendientes de revisión.")
                    _comprobar_entradas(carpeta, set(usuarios))
                    filas = {}
                    sin_historial = sin_comprobante = 0
                    for usuario in usuarios:
                        historial = _historial_de_cuenta(carpeta, usuario)
                        if historial.ruta != historiales[usuario].ruta:
                            raise ImportacionFlotaError("La ruta de un historial ha cambiado.")
                        if historial.ruta.exists() and (os.path.samefile(historial.ruta, base) or os.path.samefile(historial.ruta, destino)):
                            raise ImportacionFlotaError("Los archivos de origen y destino deben ser distintos.")
                        if not historial.ruta.exists():
                            sin_historial += 1
                        registros = historial._consultar_sin_bloqueo()
                        propias = {}
                        for fila in registros:
                            _validar_fila(fila)
                            identificador = fila['identificador']
                            if identificador in filas:
                                raise ImportacionFlotaError("Hay identificadores de carrera duplicados.")
                            filas[identificador] = (usuario, fila)
                            propias[identificador] = fila
                        confirmadas = cuentas.execute("SELECT id FROM carreras_recuperacion WHERE usuario_id=? AND estado_registro='guardada'", (usuario,)).fetchall()
                        ids_confirmados = {registro["id"] for registro in confirmadas}
                        sin_comprobante += len(set(propias) - ids_confirmados)
                        for registro in confirmadas:
                            _, _, resumen = _leer(cuentas, usuario, registro['id'])
                            if propias.get(registro['id']) != _fila(resumen):
                                raise ImportacionFlotaError("Un cierre confirmado no coincide con su CSV.")
                    legado = (carpeta / 'historial.csv').exists()
                    if activar and (legado or sin_historial or sin_comprobante):
                        raise ImportacionFlotaError("La activación necesita revisar historiales antiguos, ausentes o sin comprobante.")
                    identidad = None
                    with closing(sqlite3.connect(destino.as_uri() + '?mode=rw', uri=True, timeout=5, isolation_level=None)) as sqlite:
                        sqlite.row_factory = sqlite3.Row
                        sqlite.execute('BEGIN IMMEDIATE')
                        try:
                            HistorialSQLite._verificar(sqlite, permitir_creacion=False)
                            columnas = ', '.join(CAMPOS)
                            existentes = sqlite.execute(f'SELECT {columnas}, usuario_id FROM carreras').fetchall()
                            identificadores = [fila['identificador'] for fila in existentes]
                            if len(identificadores) != len(set(identificadores)):
                                raise ImportacionFlotaError("El destino contiene identificadores duplicados.")
                            for anterior in existentes:
                                origen = filas.get(anterior['identificador'])
                                if origen is None or origen[0] != anterior['usuario_id'] or any(anterior[c] != origen[1][c] for c in CAMPOS):
                                    raise ImportacionFlotaError("El destino contiene una carrera ajena o diferente.")
                            ya = {f['identificador'] for f in existentes}
                            marcadores = ', '.join('?' for _ in CAMPOS)
                            for identificador, (usuario, fila) in filas.items():
                                if identificador not in ya:
                                    sqlite.execute(f'INSERT INTO carreras ({columnas}, usuario_id) VALUES ({marcadores}, ?)',
                                                   (*[fila[c] for c in CAMPOS], usuario))
                            verificadas = sqlite.execute(f'SELECT {columnas}, usuario_id FROM carreras').fetchall()
                            comprobadas = {f['identificador']: (f['usuario_id'], {c: f[c] for c in CAMPOS}) for f in verificadas}
                            if len(verificadas) != len(comprobadas) or comprobadas != filas:
                                raise ImportacionFlotaError("El destino no coincide exactamente con los historiales de origen.")
                            if activar:
                                identidad = _preparar_en_transaccion(sqlite)
                                if _leer_identidad(sqlite) != identidad:
                                    raise ImportacionFlotaError("La identidad SQLite necesita revisión.")
                            sqlite.commit()
                        except BaseException:
                            sqlite.rollback()
                            raise
                    if activar:
                        _cambiar_vinculo(cuentas, vinculo['identificador'], identidad)
                        try:
                            cuentas.commit()
                        except Exception as error:
                            raise ImportacionFlotaError("No se pudo confirmar el vínculo; revisa el estado antes de continuar.") from error
                    else:
                        cuentas.rollback()
                    return ResultadoImportacionFlota(len(usuarios), len(filas), len(filas) - len(ya), len(ya),
                                                     legado, sin_historial, sin_comprobante, identidad)
                except BaseException:
                    cuentas.rollback()
                    raise


def _cambiar_vinculo(conexion, identidad_csv, identidad_sqlite):
    """Actualiza el vínculo dentro de la transacción de cuentas ya retenida."""
    actualizado = conexion.execute("""UPDATE vinculo_historial SET formato='sqlite', identificador=?, estado='listo'
        WHERE id=1 AND formato='csv' AND identificador=? AND estado='listo'""", (identidad_sqlite, identidad_csv))
    if actualizado.rowcount != 1:
        raise ImportacionFlotaError("El vínculo CSV cambió durante la activación.")
    registro = _registro(conexion)
    if registro is None or tuple(registro) != ('sqlite', identidad_sqlite, 'listo'):
        raise ImportacionFlotaError("No se pudo comprobar el nuevo vínculo SQLite.")


def importar_flota_csv(ruta_cuentas, carpeta_historial, ruta_destino):
    """Importa sin cambiar el vínculo de almacenamiento."""
    return _importar_flota_csv(ruta_cuentas, carpeta_historial, ruta_destino, activar=False)


def activar_flota_sqlite(ruta_cuentas, carpeta_historial, ruta_destino):
    """Importa y cambia el vínculo bajo el bloqueo cooperativo de la instalación.

    Detiene la operación ante datos sin comprobar. La identidad y las filas se
    confirman en SQLite antes del vínculo de cuentas. No elimina los CSV ni
    habilita por sí sola el arranque web; no garantiza atomicidad entre bases.
    """
    return _importar_flota_csv(ruta_cuentas, carpeta_historial, ruta_destino, activar=True)
