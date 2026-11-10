"""Preparación de archivos experimentales para fase 3; no crea cuentas."""
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import sys

from taxiroute.base_cuentas import inicializar_base
from taxiroute.configuracion import guardar_tarifas
from taxiroute.recuperar_admin_local import _Argumentos

MARCADOR_PREPARACION = "TaxiRoute: preparación experimental CSV, versión 1.\n"


@dataclass(frozen=True)
class ArchivosCSV:
    carpeta: Path
    cuentas: Path
    tarifas: Path
    historiales: Path


def preparar_archivos_csv(carpeta):
    """Crea solo una carpeta nueva fuera de Git y sus archivos iniciales.

    El directorio padre debe existir. Si falla alguna operación posterior a la
    creación, se conserva la carpeta incompleta para revisión. No importa datos,
    no configura Gmail y no inicia un servidor. Los permisos efectivos en
    Windows deben comprobarse aparte antes de utilizar datos reales.
    """
    solicitada = Path(carpeta).expanduser()
    if solicitada.name in ("", ".", ".."):
        raise ValueError("Selecciona una carpeta nueva con nombre.")
    padre = solicitada.parent.resolve(strict=True)
    if not padre.is_dir():
        raise ValueError("La carpeta padre no es válida.")
    if any((directorio / ".git").exists() or (directorio / ".git").is_symlink()
           for directorio in (padre, *padre.parents)):
        raise ValueError("La carpeta debe estar fuera de un repositorio Git.")
    destino = padre / solicitada.name
    # Una creación exclusiva impide reutilizar carpetas, archivos o enlaces.
    destino.mkdir(mode=0o700)
    cuentas = destino / "cuentas.sqlite3"
    tarifas = destino / "tarifas.json"
    historiales = destino / "historiales"
    historiales.mkdir(mode=0o700)
    inicializar_base(cuentas)
    guardar_tarifas(tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
    with (destino / "PREPARACION_EXPERIMENTAL.txt").open("x", encoding="utf-8", newline="\n") as archivo:
        archivo.write(MARCADOR_PREPARACION)
    return ArchivosCSV(destino, cuentas, tarifas, historiales)


def main(argv=None):
    parser = _Argumentos(description="Prepara archivos CSV experimentales en una carpeta nueva.")
    parser.add_argument("--carpeta", required=True, type=Path, help="Carpeta nueva fuera de un repositorio Git")
    argumentos = parser.parse_args(argv)
    try:
        preparar_archivos_csv(argumentos.carpeta)
    except FileExistsError:
        print("La carpeta o el archivo ya existe; no se ha sustituido. Revisa cualquier preparación parcial.", file=sys.stderr)
        return 1
    except (ValueError, OSError, sqlite3.Error):
        print("No se ha podido completar la preparación. Si se creó una carpeta parcial, revísala antes de reintentar.", file=sys.stderr)
        return 1
    print("Archivos experimentales preparados. Todavía no hay cuentas, correo ni servidor configurados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
