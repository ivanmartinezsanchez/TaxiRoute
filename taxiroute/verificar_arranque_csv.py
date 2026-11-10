"""Comprobación experimental de historiales CSV antes de iniciar la flota."""
from contextlib import ExitStack, closing
import os
from pathlib import Path

from taxiroute.base_cuentas import IDENTIFICADOR_APLICACION, VERSION_ESQUEMA
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import ConciliacionCSVError, _conectar_existente, _leer
from taxiroute.historial import _fila
from taxiroute.rutas_csv import _historial_de_cuenta


def _comprobar_base(conexion):
    if (conexion.execute("PRAGMA application_id").fetchone()[0] != IDENTIFICADOR_APLICACION
            or conexion.execute("PRAGMA user_version").fetchone()[0] != VERSION_ESQUEMA):
        raise ConciliacionCSVError("La base de recuperación no es compatible.")


def _usuarios_guardados(conexion):
    _comprobar_base(conexion)
    return tuple(fila[0] for fila in conexion.execute(
        "SELECT DISTINCT usuario_id FROM carreras_recuperacion WHERE estado_registro='guardada' ORDER BY usuario_id"))


def verificar_historiales_guardados_csv(ruta_base, carpeta_historial):
    """Verifica todos los cierres confirmados antes de servir solicitudes.

    El llamador debe mantener el bloqueo de instancia durante esta función y
    toda la ejecución; también debe verificar el vínculo de la instalación.
    Se toman todos los bloqueos de CSV por orden de usuario y después una
    transacción de cuentas; se rechaza si cambió el conjunto de propietarios.
    Los cierres antiguos sin comprobante durable no quedan verificados. No se
    reparan archivos y el bloqueo solo coordina procesos que lo respeten.
    Devuelve el número de carreras confirmadas verificadas.
    """
    base, carpeta = Path(ruta_base), Path(carpeta_historial)
    if base.is_symlink() or not base.is_file() or carpeta.is_symlink() or not carpeta.is_dir():
        raise ConciliacionCSVError("No están disponibles la base y la carpeta de historiales.")
    carpeta = carpeta.resolve()
    with closing(_conectar_existente(base)) as conexion:
        usuarios = _usuarios_guardados(conexion)
    historiales = {}
    for usuario in usuarios:
        historial = _historial_de_cuenta(carpeta, usuario)
        csv = historial.ruta
        if not csv.is_file() or csv.is_symlink() or os.path.samefile(base, csv):
            raise ConciliacionCSVError("Falta un historial confirmado o no es independiente.")
        historiales[usuario] = historial
    with ExitStack() as bloqueos:
        for usuario in usuarios:
            bloqueos.enter_context(bloqueo_historial(historiales[usuario].ruta))
        with closing(_conectar_existente(base)) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                if _usuarios_guardados(conexion) != usuarios:
                    raise ConciliacionCSVError("Los historiales cambiaron durante la verificación.")
                total = 0
                for usuario in usuarios:
                    historial = _historial_de_cuenta(carpeta, usuario)
                    if historial.ruta != historiales[usuario].ruta or not historial.ruta.is_file():
                        raise ConciliacionCSVError("Falta un historial confirmado.")
                    if os.path.samefile(base, historial.ruta):
                        raise ConciliacionCSVError("La base y el historial deben ser distintos.")
                    filas = historial._consultar_sin_bloqueo()
                    por_id = {}
                    for fila in filas:
                        por_id.setdefault(fila["identificador"], []).append(fila)
                    registros = conexion.execute("""SELECT id FROM carreras_recuperacion
                        WHERE usuario_id=? AND estado_registro='guardada'""", (usuario,)).fetchall()
                    for registro in registros:
                        _, _, resumen = _leer(conexion, usuario, registro["id"])
                        if por_id.get(registro["id"]) != [_fila(resumen)]:
                            raise ConciliacionCSVError("Una carrera confirmada no coincide con el historial.")
                        total += 1
                conexion.rollback()
                return total
            except BaseException:
                conexion.rollback()
                raise
