"""Lanzador local experimental de TaxiRoute con historial SQLite.

Requiere una instalación preparada y certificados proporcionados por la
persona usuaria. No es un servidor de producción ni habilita acceso de red.
"""
from pathlib import Path
import sqlite3
import ssl
import sys

from taxiroute.recuperar_admin_local import _Argumentos
from taxiroute.servidor_sqlite_local import servidor_sqlite_experimental


def _enviar_pin(destinatario, codigo):
    # No se accede al almacén ni a Gmail hasta que el administrador lo solicita.
    from taxiroute.correo_gmail import EnviadorGmail
    from taxiroute.oauth_gmail import cliente_gmail_autorizado
    EnviadorGmail(cliente_gmail_autorizado())(destinatario, codigo)


def main(argv=None):
    parser = _Argumentos(description='Servidor local experimental con historial SQLite.')
    parser.add_argument('--cuentas', required=True, type=Path, help='Base de cuentas vinculada')
    parser.add_argument('--historial', required=True, type=Path, help='Base de historial SQLite vinculada')
    parser.add_argument('--tarifas', required=True, type=Path, help='Archivo de tarifas existente')
    parser.add_argument('--certificado', required=True, type=Path, help='Certificado TLS PEM')
    parser.add_argument('--clave', required=True, type=Path, help='Clave TLS PEM')
    parser.add_argument('--puerto', type=int, default=8769, help='Puerto local (por defecto 8769)')
    args = parser.parse_args(argv)
    if not 1 <= args.puerto <= 65535:
        parser.error('El puerto no es válido.')
    try:
        with servidor_sqlite_experimental(ruta_cuentas=args.cuentas, ruta_historial=args.historial,
                ruta_tarifas=args.tarifas, certificado=args.certificado, clave=args.clave,
                puerto=args.puerto, enviar_pin=_enviar_pin) as servidor:
            print(f'Servidor local experimental: https://127.0.0.1:{args.puerto}/', flush=True)
            print('Solo datos de prueba. No es una instalación de producción.', flush=True)
            print('El acceso administrativo requiere correo autorizado para recibir el código.', flush=True)
            try:
                servidor.serve_forever()
            except KeyboardInterrupt:
                print('\nServidor detenido.', flush=True)
    except (ValueError, OSError, sqlite3.Error, ssl.SSLError):
        print('No se ha podido iniciar. Revisa la instalación, el historial, TLS y el puerto.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
