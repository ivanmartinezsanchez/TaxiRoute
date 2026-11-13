"""Primer arranque interactivo de una instalación local propia; experimental."""
import getpass
import os
from pathlib import Path
import sqlite3
import sys
import warnings

from taxiroute.arrancar_windows import ErrorArranque, arrancar
from taxiroute.cargar_gmail_windows import _docker
from taxiroute.oauth_gmail import CLAVE_ALMACEN_ALTERNATIVA, SERVICIO_ALMACEN, _almacen_windows
from taxiroute.oauth_gmail_contenedor import _validar_contenido
from taxiroute.cuentas import DatosCuentaInvalidos
from taxiroute.inicializacion_administrador import InicializacionCancelada
from taxiroute.instalacion_inicial import preparar_instalacion, revisar_instalacion
from taxiroute.recuperar_admin_local import _Argumentos


class ErrorPrimerArranque(ValueError):
    """Un requisito o dato de entrada necesita atención de la persona usuaria."""


def _carpeta_predeterminada():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ErrorPrimerArranque("No se encuentra la carpeta local de Windows.")
    return Path(base) / "TaxiRoute-MVP"


def _comprobar_requisitos():
    """Comprueba Docker y el perfil antes de solicitar o crear datos."""
    try:
        _docker(["version", "--format", "{{.Server.Version}}"])
        contenido = _almacen_windows().get_password(SERVICIO_ALMACEN, CLAVE_ALMACEN_ALTERNATIVA)
        if not isinstance(contenido, str):
            raise ValueError
        _validar_contenido(contenido.encode("utf-8"))
    except Exception:
        raise ErrorPrimerArranque("Comprueba que Docker Desktop esté abierto y que Gmail esté autorizado en Windows.") from None


def _crear(carpeta, *, comprobar_requisitos=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ErrorPrimerArranque("Para crear la cuenta necesitas una terminal interactiva.")
    (_comprobar_requisitos if comprobar_requisitos is None else comprobar_requisitos)()
    print("Primera ejecución: se creará una instalación nueva, sin importar datos anteriores.")
    print("La contraseña temporal debe tener al menos 12 caracteres, una mayúscula, tres minúsculas, tres números y un símbolo.")
    usuario = input("Usuario administrador [Admin]: ").strip() or "Admin"
    nombre = input("Nombre visible [Administrador]: ").strip() or "Administrador"
    correo = input("Correo que recibirá el código de administrador: ").strip()
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        clave = getpass.getpass("Contraseña temporal del administrador: ")
        repetida = getpass.getpass("Repite la contraseña: ")
    if clave != repetida:
        raise ErrorPrimerArranque("Las contraseñas no coinciden; no se ha creado la instalación.")
    repetida = None
    def confirmar_codigos(codigos):
        print("\nGuarda estos cinco códigos de recuperación en un lugar privado.")
        print("Sirven para desbloquear la cuenta con su contraseña; no sustituyen la contraseña ni el correo.")
        print("La cuenta solo se creará si confirmas; pueden quedar visibles en la terminal.")
        for codigo in codigos:
            print(codigo)
        return input("Escribe GUARDADOS para continuar, o pulsa Intro para cancelar: ").strip().upper() == "GUARDADOS"
    try:
        return preparar_instalacion(carpeta, usuario=usuario, nombre=nombre, correo=correo,
                                   contrasena=clave, confirmar_codigos=confirmar_codigos)
    finally:
        clave = None


def main(argv=None):
    parser = _Argumentos(description="Inicia TaxiRoute local y prepara una instalación nueva si es necesario.")
    parser.add_argument("--carpeta", type=Path, help="Carpeta de instalación; por defecto, la carpeta local de Windows")
    args = parser.parse_args(argv)
    try:
        carpeta = args.carpeta if args.carpeta is not None else _carpeta_predeterminada()
        print("TaxiRoute: instalación local experimental. No usa datos de la demo ni importa datos anteriores.")
        print("Aún no está validada para datos o cobros reales: quedan pendientes protección de archivos y despliegue final.")
        if carpeta.exists() or carpeta.is_symlink():
            rutas = revisar_instalacion(carpeta)
        else:
            rutas = _crear(carpeta)
            print("Instalación creada. La contraseña es temporal y deberás cambiarla tras verificar el correo.")
        mensaje = arrancar(rutas.datos, rutas.certificado, rutas.clave)
        print(mensaje)
        print("Acceso local: https://127.0.0.1:18770/")
        print("El certificado local es autofirmado y el navegador puede mostrar una advertencia.")
        return 0
    except getpass.GetPassWarning:
        print("La terminal no puede ocultar la contraseña. Usa una terminal interactiva compatible.", file=sys.stderr)
    except (EOFError, KeyboardInterrupt, InicializacionCancelada):
        print("Operación cancelada. Si se mostraron códigos, consérvalos y revisa la carpeta antes de reintentar.", file=sys.stderr)
        return 130
    except ErrorPrimerArranque as error:
        print(str(error), file=sys.stderr)
    except ErrorArranque as error:
        print(str(error), file=sys.stderr)
    except (ValueError, DatosCuentaInvalidos, OSError, sqlite3.Error):
        print("No se pudo iniciar TaxiRoute. Si se creó una carpeta parcial, no la borres: consérvala y revisa su estado y los códigos antes de reintentar.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
