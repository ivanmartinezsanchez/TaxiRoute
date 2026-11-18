"""Asistente explícito para preparar una transición local a red experimental.

No restaura copias completas ni establece confianza TLS en los dispositivos.
Mantiene las comprobaciones y barreras de los componentes que utiliza.
"""
import argparse
import os
from pathlib import Path
import sys

from taxiroute.arranque_red import _PROYECTO
from taxiroute.certificados_lan import verificar_certificados_preparados
from taxiroute.iniciar_red_existente import main as iniciar_red
from taxiroute.permisos_proxy_windows_experimental import verificar_permisos_windows
from taxiroute.red_local import comprobar_direccion_local
from taxiroute.transicion_local_red import transicionar_local_a_red
from taxiroute.verificar_copia_previa import verificar_copia_previa


def _preparar(*, origen, copia, destino, direccion, puerto, proyecto, certificados_preparados=None):
    """Comprueba el destino y los requisitos antes de iniciar la transición.

    La comprobación no reserva el estado: la transición vuelve a comprobarlo.
    """
    origen, copia, destino = (Path(p).expanduser().absolute() for p in (origen, copia, destino))
    if (type(puerto) is not int or not 1 <= puerto <= 65535
            or not isinstance(proyecto, str) or not _PROYECTO.fullmatch(proyecto)):
        raise ValueError('El puerto o el nombre del proyecto no son válidos.')
    comprobar_direccion_local(direccion)
    verificar_permisos_windows(destino, permitir_ausente=True)
    padre = destino.parent.resolve(strict=True)
    destino = padre / destino.name
    if os.path.lexists(destino):
        raise ValueError('La carpeta de destino ya existe.')
    for ruta in (origen.resolve(strict=True), copia.resolve(strict=True)):
        if destino == ruta or ruta in destino.parents or destino in ruta.parents:
            raise ValueError('El destino debe estar separado del origen y la copia.')
    if any(os.path.lexists(p / '.git') for p in (padre, *padre.parents)):
        raise ValueError('El destino debe estar fuera de un repositorio Git.')
    if certificados_preparados is not None:
        certificados_preparados = Path(certificados_preparados).expanduser().absolute()
        verificar_certificados_preparados(certificados_preparados, direccion=direccion,
            comprobar_permisos=verificar_permisos_windows)
        preparada = certificados_preparados.resolve(strict=True)
        for otra in (origen.resolve(strict=True), copia.resolve(strict=True), destino):
            if preparada == otra or otra in preparada.parents or preparada in otra.parents:
                raise ValueError('Los certificados deben estar separados de las instalaciones.')
    verificar_copia_previa(origen, copia)
    opciones = dict(origen=origen, copia=copia, destino=destino, direccion=direccion,
                    puerto=puerto, proyecto=proyecto)
    if certificados_preparados is not None:
        opciones['certificados_preparados'] = certificados_preparados
    return opciones


def main(argv=None):
    parser = argparse.ArgumentParser(description='Transición experimental de TaxiRoute local a LAN.')
    parser.add_argument('--origen', type=Path, required=True, help='Instalación local detenida')
    parser.add_argument('--copia', type=Path, required=True, help='Copia previa comprobada')
    parser.add_argument('--destino', type=Path, required=True, help='Carpeta nueva para la instalación LAN')
    parser.add_argument('--direccion', required=True, help='IPv4 privada asignada a este equipo')
    parser.add_argument('--puerto', type=int, required=True, help='Puerto HTTPS de la red local')
    parser.add_argument('--proyecto', required=True, help='Nombre Docker nuevo')
    parser.add_argument('--certificados-lan', type=Path, help='Carpeta de certificados LAN previamente preparados')
    parser.add_argument('--conectar-gmail', action='store_true', help='Cargar expresamente el perfil Gmail autorizado')
    args = parser.parse_args(argv)
    try:
        opciones = _preparar(origen=args.origen, copia=args.copia, destino=args.destino,
                             direccion=args.direccion, puerto=args.puerto, proyecto=args.proyecto,
                             certificados_preparados=args.certificados_lan)
    except Exception:
        print('No se han superado las comprobaciones previas; no se ha iniciado la transición.', file=sys.stderr)
        return 1
    print('Comprobaciones previas superadas. Preparando la instalación de red.', flush=True)
    try:
        resultado = transicionar_local_a_red(**opciones)
    except Exception:
        print('La transición no se ha confirmado. Conserva las instalaciones y cualquier marcador para revisión.', file=sys.stderr)
        return 1
    print('Instalación de red creada. La instalación local anterior mantiene una barrera de transición.', flush=True)
    opciones_inicio = ['--identidad', str(resultado.identidad)]
    if args.conectar_gmail:
        opciones_inicio.append('--conectar-gmail')
    try:
        codigo = iniciar_red(opciones_inicio)
    except Exception:
        codigo = 1
    if codigo != 0:
        print('La instalación de red puede estar activa, pero no se ha confirmado su inicio completo. No borres ni reinicies las instalaciones; requieren revisión.', file=sys.stderr)
        return 1
    print('Instala únicamente el certificado público autoridad.cer en cada dispositivo y verifica HTTPS antes de usar credenciales reales.')
    print('Los CSV históricos y el registro de actividad permanecen en la instalación anterior.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
