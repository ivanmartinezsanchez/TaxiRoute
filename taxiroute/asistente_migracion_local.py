"""Asistente local experimental para la transición de historial CSV a SQLite.

No es una instalación de producción. Requiere detener la aplicación y revisar
copias de seguridad; no borra ni modifica los archivos CSV originales.
"""
from contextlib import closing
import getpass
from pathlib import Path
from secrets import token_bytes
import sqlite3
import sys
import warnings

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.importacion_flota_csv import activar_flota_sqlite, ImportacionFlotaError
from taxiroute.pin import ServicioPIN
from taxiroute.recuperar_admin_local import _Argumentos
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.sesiones import ServicioSesiones
from taxiroute.vinculo_historial import verificar_vinculo_csv


def _cuentas_sin_csv(base, carpeta):
    """Instantánea informativa; la activación vuelve a verificarla bajo bloqueo."""
    if (base.parent / 'servidor.lock').is_symlink():
        raise ValueError('No se puede utilizar el bloqueo de la instalación.')
    with bloqueo_historial(base.parent / 'servidor', espera=0):
        if not verificar_vinculo_csv(base, carpeta):
            raise ValueError('La instalación CSV necesita revisión.')
        with closing(_conectar_existente(base)) as conexion:
            cuentas = conexion.execute('SELECT id, nombre_visible FROM usuarios ORDER BY id').fetchall()
            return [(fila['id'], fila['nombre_visible']) for fila in cuentas
                    if not _historial_de_cuenta(carpeta, fila['id']).ruta.exists()]


def _gmail(destinatario, codigo):
    # El usuario inicia explícitamente el asistente; no se accede al importar.
    from taxiroute.correo_gmail import EnviadorGmail
    from taxiroute.oauth_gmail import cliente_gmail_autorizado
    EnviadorGmail(cliente_gmail_autorizado())(destinatario, codigo)


def main(argv=None, *, leer=None, secreto=None, enviar_pin=None, escribir=None, interactiva=None):
    parser = _Argumentos(description='Asistente local experimental de migración a SQLite.')
    parser.add_argument('--cuentas', required=True, type=Path, help='Base de cuentas existente')
    parser.add_argument('--historiales', required=True, type=Path, help='Carpeta de historiales CSV vinculada')
    parser.add_argument('--destino', required=True, type=Path, help='Archivo SQLite de destino')
    args = parser.parse_args(argv)
    leer = input if leer is None else leer
    secreto = getpass.getpass if secreto is None else secreto
    escribir = print if escribir is None else escribir
    enviar_pin = _gmail if enviar_pin is None else enviar_pin
    if interactiva is None:
        interactiva = sys.stdin.isatty()
    if not interactiva:
        escribir('Abre una terminal interactiva para realizar la revisión de forma privada.')
        return 2
    base, carpeta, destino = args.cuentas, args.historiales, args.destino
    if (base.is_symlink() or not base.is_file() or carpeta.is_symlink() or not carpeta.is_dir()
            or destino.is_symlink() or not destino.parent.is_dir()):
        escribir('Comprueba las rutas: se necesitan cuentas y carpeta existentes y un destino válido.')
        return 2
    base, carpeta, destino = base.resolve(), carpeta.resolve(), destino.resolve()
    if destino == base or destino.is_relative_to(carpeta):
        escribir('El destino debe ser independiente de las cuentas y de la carpeta CSV.')
        return 2
    if (base.parent / 'servidor.lock').is_symlink():
        escribir('No se puede utilizar el bloqueo de la instalación.')
        return 2
    token = None
    try:
        # Comprobar exclusión antes de solicitar y enviar un código.
        with bloqueo_historial(base.parent / 'servidor', espera=0):
            if not verificar_vinculo_csv(base, carpeta):
                raise ValueError('La instalación CSV necesita revisión.')
        escribir('Detén TaxiRoute y conserva una copia de seguridad antes de continuar.')
        escribir('Se enviará un código al correo configurado para el administrador.')
        usuario = leer('Usuario administrador: ')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            clave = secreto('Contraseña: ')
        pin = ServicioPIN(base, clave_hmac=token_bytes(32), enviar=enviar_pin)
        desafio = pin.solicitar(usuario, clave)
        clave = None
        if desafio is None:
            escribir('No se ha podido continuar. Comprueba el acceso y el envío de correo.')
            return 1
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            codigo = secreto('Código recibido por correo: ')
        verificado = pin.verificar(desafio, codigo)
        codigo = None
        if verificado is None or verificado.sesion is None:
            escribir('No se ha podido verificar el acceso o hay que cambiar la contraseña primero.')
            return 1
        token = verificado.sesion.token
        ausentes = _cuentas_sin_csv(base, carpeta)
        revisadas = []
        for usuario_id, nombre in ausentes:
            escribir(f'No hay CSV para {nombre} ({usuario_id}). La ausencia no demuestra que no hubiera carreras.')
            if leer('Tras revisar este caso, escribe REVISADO para confirmarlo: ').strip() != 'REVISADO':
                escribir('Migración cancelada; no se ha cambiado el almacenamiento.')
                return 1
            revisadas.append(usuario_id)
        escribir('La aplicación volverá a comprobar las cuentas y los archivos antes de cambiar el vínculo.')
        if leer('Para continuar, escribe MIGRAR: ').strip() != 'MIGRAR':
            escribir('Migración cancelada; no se ha cambiado el almacenamiento.')
            return 1
        # La creación es exclusiva y nunca sobrescribe un archivo existente.
        if not destino.exists():
            with destino.open('xb'):
                pass
            HistorialSQLite(destino).inicializar()
        resultado = activar_flota_sqlite(base, carpeta, destino, token_admin=token,
                                        cuentas_revisadas=revisadas, confirmacion=True)
        escribir(f'Migración experimental completada: {resultado.filas_origen} carreras verificadas; los CSV se conservan.')
        return 0
    except getpass.GetPassWarning:
        escribir('No se pueden ocultar los datos en esta terminal. Abre otra terminal interactiva.')
        return 2
    except (EOFError, KeyboardInterrupt):
        escribir('Operación cancelada. Comprueba el estado antes de volver a intentarlo.')
        return 130
    except ImportacionFlotaError as error:
        escribir(f'No se ha completado la migración: {error}')
        return 1
    except (OSError, sqlite3.Error, ValueError):
        escribir('No se ha podido completar la operación. Comprueba las rutas y el estado de la instalación.')
        return 1
    finally:
        if token is not None:
            try:
                ServicioSesiones(base).cerrar(token)
            except (OSError, sqlite3.Error, ValueError):
                escribir('No se ha podido confirmar el cierre de la sesión temporal.')


if __name__ == '__main__':
    raise SystemExit(main())
