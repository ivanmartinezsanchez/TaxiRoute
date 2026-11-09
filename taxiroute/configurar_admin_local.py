"""Alta inicial local experimental; no inicia un servidor ni configura Gmail."""
import getpass
from pathlib import Path
import sqlite3
import sys
import warnings

from taxiroute.cuentas import DatosCuentaInvalidos
from taxiroute.inicializacion_administrador import (
    InicializacionCancelada, InicializacionDenegada, preparar_primer_administrador_con_codigos,
)
from taxiroute.recuperar_admin_local import _Argumentos


def main(argv=None):
    parser = _Argumentos(description="Preparación local experimental del primer administrador.")
    parser.add_argument("--cuentas", required=True, type=Path, help="Ruta a la base de cuentas existente")
    argumentos = parser.parse_args(argv)
    if argumentos.cuentas.is_symlink() or not argumentos.cuentas.is_file():
        print("No se encuentra una base de cuentas válida.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Se necesita una terminal interactiva para proteger los datos y mostrar los códigos.", file=sys.stderr)
        return 2
    clave = None
    confirmar_clave = None
    try:
        usuario = input("Usuario administrador: ")
        nombre = input("Nombre visible: ")
        correo = input("Correo para verificar el acceso: ")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            clave = getpass.getpass("Contraseña temporal: ")
            confirmar_clave = getpass.getpass("Repite la contraseña temporal: ")
        if clave != confirmar_clave:
            print("Las contraseñas no coinciden; no se ha creado la cuenta.", file=sys.stderr)
            return 1
        confirmar_clave = None

        def confirmar_codigos(codigos):
            print("\nGuarda estos códigos de recuperación en un lugar privado. Cada uno se puede usar una vez.")
            print("Aún no son válidos: la cuenta solo se creará si confirmas y aparece el mensaje de éxito.")
            for codigo in codigos:
                print(codigo)
            print("Los códigos pueden quedar visibles en el historial de la terminal.", flush=True)
            return input("Escribe GUARDADOS para crear la cuenta, o pulsa Intro para cancelar: ").strip().upper() == "GUARDADOS"

        preparar_primer_administrador_con_codigos(argumentos.cuentas, nombre_usuario=usuario,
            nombre_visible=nombre, contrasena=clave, correo=correo, confirmar_codigos=confirmar_codigos)
        print("Cuenta creada. Los códigos guardados ya son válidos. Debes cambiar la contraseña al primer acceso.", flush=True)
        return 0
    except getpass.GetPassWarning:
        print("No se puede ocultar la contraseña en esta terminal; no se ha creado la cuenta.", file=sys.stderr)
        return 2
    except InicializacionCancelada:
        print("\nOperación cancelada. La cuenta no se ha creado y los códigos mostrados no son válidos.", file=sys.stderr)
        return 130
    except (EOFError, KeyboardInterrupt):
        print("\nOperación interrumpida. Si se mostraron códigos, consérvalos y revisa el estado de la base antes de reintentar.", file=sys.stderr)
        return 130
    except (InicializacionDenegada, DatosCuentaInvalidos):
        print("No se ha creado la cuenta; los códigos mostrados no están activos. Comprueba los datos y la base.", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, UnicodeError):
        print("No se ha podido confirmar el resultado. Conserva los códigos mostrados y revisa la base antes de reintentar.", file=sys.stderr)
        return 1
    finally:
        clave = None
        confirmar_clave = None
if __name__ == "__main__":
    raise SystemExit(main())
