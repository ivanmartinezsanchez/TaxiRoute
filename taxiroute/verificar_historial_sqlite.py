"""Comprobaciones experimentales antes de usar un historial SQLite vinculado.

El llamador debe mantener el bloqueo cooperativo de la instalación durante
esta comprobación y todo el uso posterior. No cambia archivos ni repara datos.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from taxiroute.conciliacion_csv import _conectar_existente, _leer as leer_cierre
from taxiroute.historial import CAMPOS, _fila
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.identidad_historial_sqlite import _leer as leer_identidad, _leer_procedencia
from taxiroute.migracion_historial import _validar_fila
from taxiroute.vinculo_historial import _registro


class VerificacionHistorialSQLiteError(ValueError):
    """El historial vinculado no corresponde a las cuentas o necesita revisión."""


def verificar_historial_sqlite_vinculado(ruta_cuentas, ruta_historial):
    """Comprueba identidad y cierres confirmados; requiere exclusión externa.

    Las dos bases tienen transacciones independientes. El bloqueo del servidor
    debe permanecer adquirido y otros programas deben respetarlo. No detecta
    todas las restauraciones de copias antiguas ni cambios no cooperativos.
    """
    cuentas, historial = Path(ruta_cuentas), Path(ruta_historial)
    if any(p.is_symlink() or not p.is_file() for p in (cuentas, historial)):
        raise VerificacionHistorialSQLiteError("Las bases deben existir y no ser enlaces.")
    if os.path.samefile(cuentas, historial):
        raise VerificacionHistorialSQLiteError("Las bases de cuentas e historial deben ser distintas.")
    with closing(_conectar_existente(cuentas)) as conexion:
        conexion.execute('BEGIN')
        vinculo = _registro(conexion)
        if vinculo is None or vinculo['formato'] != 'sqlite' or vinculo['estado'] != 'listo':
            raise VerificacionHistorialSQLiteError("El historial SQLite no está vinculado y listo.")
        if (conexion.execute('PRAGMA foreign_key_check').fetchone()
                or conexion.execute('SELECT 1 FROM carreras_recuperacion c LEFT JOIN usuarios u ON u.id=c.usuario_id WHERE u.id IS NULL LIMIT 1').fetchone()):
            raise VerificacionHistorialSQLiteError("Hay referencias de cuenta inválidas.")
        procedencias = conexion.execute('SELECT vinculo_csv, identidad_sqlite FROM procedencia_sqlite WHERE id=1').fetchall()
        if len(procedencias) != 1 or procedencias[0]['identidad_sqlite'] != vinculo['identificador']:
            raise VerificacionHistorialSQLiteError("La procedencia del historial necesita revisión.")
        procedencia_esperada = (procedencias[0]['vinculo_csv'], procedencias[0]['identidad_sqlite'])
        usuarios = {fila[0] for fila in conexion.execute('SELECT id FROM usuarios')}
        guardadas = conexion.execute("SELECT id, usuario_id FROM carreras_recuperacion WHERE estado_registro='guardada'").fetchall()
        with closing(sqlite3.connect(historial.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)) as destino:
            destino.row_factory = sqlite3.Row
            destino.execute('BEGIN')
            HistorialSQLite._verificar(destino, permitir_creacion=False)
            if leer_identidad(destino) != vinculo['identificador']:
                raise VerificacionHistorialSQLiteError("La identidad del historial no coincide con las cuentas.")
            if _leer_procedencia(destino) != procedencia_esperada:
                raise VerificacionHistorialSQLiteError("La procedencia del historial no coincide con las cuentas.")
            columnas = ', '.join(CAMPOS)
            filas = destino.execute(f'SELECT {columnas}, usuario_id FROM carreras').fetchall()
            por_id = {}
            for fila in filas:
                datos = {campo: fila[campo] for campo in CAMPOS}
                _validar_fila(datos)
                if fila['identificador'] in por_id or (fila['usuario_id'] is not None and fila['usuario_id'] not in usuarios):
                    raise VerificacionHistorialSQLiteError("El historial contiene duplicados o cuentas desconocidas.")
                por_id[fila['identificador']] = (fila['usuario_id'], datos)
            for guardada in guardadas:
                _, _, resumen = leer_cierre(conexion, guardada['usuario_id'], guardada['id'])
                if por_id.get(guardada['id']) != (guardada['usuario_id'], _fila(resumen)):
                    raise VerificacionHistorialSQLiteError("Un cierre confirmado no coincide con el historial.")
