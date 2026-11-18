"""Prepara material TLS LAN sin iniciar Docker ni consultar credenciales."""
import argparse
import os
from pathlib import Path
import sys

from taxiroute.certificados_lan import generar_certificado_lan, verificar_certificados_preparados
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.red_local import comprobar_direccion_local


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepara certificados locales para TaxiRoute.')
    parser.add_argument('--carpeta', required=True, type=Path, help='Carpeta nueva y privada')
    parser.add_argument('--direccion', required=True, help='IPv4 privada de este equipo')
    args = parser.parse_args(argv)
    try:
        carpeta = args.carpeta.expanduser().absolute()
        comprobar_direccion_local(args.direccion)
        verificar_permisos_windows(carpeta, permitir_ausente=True)
        if os.path.lexists(carpeta) or any(os.path.lexists(p/'.git') for p in (carpeta.parent, *carpeta.parent.parents)):
            raise ValueError('La carpeta ya existe o está dentro de Git.')
        carpeta.mkdir(mode=0o700)
        verificar_permisos_windows(carpeta)
        generar_certificado_lan(carpeta/'tls', carpeta/'autoridad-privada', direccion=args.direccion,
                                comprobar_permisos=verificar_permisos_windows)
        verificar_certificados_preparados(carpeta, direccion=args.direccion,
                                         comprobar_permisos=verificar_permisos_windows)
        print('Certificado público para los dispositivos:', carpeta/'tls'/'autoridad.cer')
        print('Conserva el resto de la carpeta en este equipo; contiene claves privadas.')
        return 0
    except Exception:
        print('No se pudieron preparar los certificados. Conserva cualquier carpeta parcial para revisión.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
