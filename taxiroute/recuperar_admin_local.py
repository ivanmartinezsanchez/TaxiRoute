"""Herramienta local experimental; no abre un servidor ni solicita correo."""
import argparse
import getpass
from pathlib import Path
import sqlite3
import sys
import warnings

from taxiroute.recuperacion_admin_experimental import RecuperacionDenegada, recuperar_administrador


class _Argumentos(argparse.ArgumentParser):
    def error(self, mensaje):
        # No reflejar argumentos desconocidos: podrían contener un secreto.
        self.exit(2, "Argumentos no válidos. Consulta --help.\n")


def main(argv=None):
    parser = _Argumentos(description="Recuperación local experimental de una cuenta administradora.")
    parser.add_argument("--cuentas", required=True, type=Path, help="Ruta a la base de cuentas existente")
    argumentos = parser.parse_args(argv)
    if argumentos.cuentas.is_symlink() or not argumentos.cuentas.is_file():
        print("No se encuentra una base de cuentas válida.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("Abre una terminal interactiva para introducir los datos de forma privada.", file=sys.stderr)
        return 2
    clave = None
    codigo = None
    try:
        usuario = input("Usuario administrador: ")
        with warnings.catch_warnings():
            # Si no se pueden ocultar los caracteres, getpass no debe leerlos.
            warnings.simplefilter("error", getpass.GetPassWarning)
            clave = getpass.getpass("Contraseña: ")
            codigo = getpass.getpass("Código de recuperación: ")
        recuperar_administrador(argumentos.cuentas, usuario, clave, codigo)
    except getpass.GetPassWarning:
        print("No se pueden ocultar los datos en esta terminal. Abre otra terminal interactiva.", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nOperación cancelada.", file=sys.stderr)
        return 130
    except RecuperacionDenegada:
        print("No se ha podido recuperar el acceso. Comprueba los datos y la espera entre intentos.", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, UnicodeError):
        print("No se ha podido completar la operación. Revisa el acceso a la base de cuentas.", file=sys.stderr)
        return 1
    finally:
        # Reduce referencias, pero Python no garantiza el borrado seguro de memoria.
        clave = None
        codigo = None
    print("Cuenta desbloqueada. Vuelve a iniciar sesión y completa la verificación habitual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
