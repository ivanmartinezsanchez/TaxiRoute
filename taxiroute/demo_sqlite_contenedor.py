"""Demostración Docker SQLite vinculada; únicamente datos ficticios.

El PIN se muestra para la demostración y nunca se envía correo. El certificado
es temporal y autofirmado. No utilizar con datos reales ni como producción.
"""
from contextlib import ExitStack, closing, contextmanager
import csv
from pathlib import Path
from secrets import token_bytes
import sqlite3
import ssl
import signal
from tempfile import TemporaryDirectory

from werkzeug.serving import make_server

from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_csv import preparar_demo_csv, CORREO_FICTICIO
from taxiroute.historial import CAMPOS
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.importacion_flota_csv import activar_flota_sqlite

MARCADOR_SQLITE = 'TaxiRoute: demostración SQLite vinculada ficticia, versión 1.\n'
ARCHIVO_MARCADOR_SQLITE = 'DEMO_SQLITE_FICTICIA.txt'


def preparar_demo_sqlite(carpeta):
    """Prepara una demo nueva o valida su marcador; no adopta carpetas ajenas.

    Un fallo deja la carpeta incompleta para revisión y nunca elimina archivos.
    El marcador no protege frente a manipulación local deliberada.
    """
    carpeta = Path(carpeta).expanduser()
    if carpeta.is_symlink():
        raise ValueError('La carpeta de demostración no puede ser un enlace.')
    if not carpeta.exists():
        preparar_demo_csv(carpeta)
        cuentas = carpeta / 'cuentas.sqlite3'
        historiales = carpeta / 'historiales'
        with closing(sqlite3.connect(cuentas.resolve().as_uri() + '?mode=ro', uri=True)) as conexion:
            usuarios = [fila[0] for fila in conexion.execute('SELECT id FROM usuarios ORDER BY id')]
        for usuario in usuarios:
            directorio = historiales / usuario
            directorio.mkdir(exist_ok=False)
            with (directorio / 'historial.csv').open('x', encoding='utf-8', newline='') as archivo:
                csv.DictWriter(archivo, fieldnames=CAMPOS, lineterminator='\n').writeheader()
        destino = carpeta / 'historial.sqlite3'
        HistorialSQLite(destino).inicializar()
        activar_flota_sqlite(cuentas, historiales, destino)
        with (carpeta / ARCHIVO_MARCADOR_SQLITE).open('x', encoding='utf-8', newline='\n') as marcador:
            marcador.write(MARCADOR_SQLITE)
    else:
        if not carpeta.is_dir():
            raise ValueError('La ruta de demostración no es una carpeta.')
        marcador = carpeta / ARCHIVO_MARCADOR_SQLITE
        if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding='utf-8') != MARCADOR_SQLITE:
            raise ValueError('La carpeta no es una demostración SQLite reconocida.')
        # Reutiliza la validación de origen, buzón ficticio y archivos CSV.
        preparar_demo_csv(carpeta)
    historial = carpeta / 'historial.sqlite3'
    if historial.is_symlink() or not historial.is_file():
        raise ValueError('La demostración no tiene un historial SQLite válido.')
    return carpeta.resolve()


@contextmanager
def abrir_demo_sqlite(carpeta, *, mostrar_codigo):
    """Abre la demo bajo el bloqueo de la instalación; no contacta con Gmail."""
    if not callable(mostrar_codigo):
        raise TypeError('Se necesita una función para mostrar el PIN ficticio.')
    carpeta = preparar_demo_sqlite(carpeta)
    def entregar(destinatario, codigo):
        if destinatario != CORREO_FICTICIO:
            raise ValueError('La demostración solo admite su buzón ficticio.')
        mostrar_codigo(codigo)
    with abrir_app_experimental(ruta_cuentas=carpeta / 'cuentas.sqlite3',
            ruta_historial=carpeta / 'historial.sqlite3', ruta_tarifas=carpeta / 'tarifas.json',
            clave_hmac=token_bytes(32), enviar_pin=entregar) as app:
        app.config['DEMO_PERSISTENTE'] = True
        app.config['PIN_DEMO_TERMINAL'] = True
        yield app


def main():
    # Fuera de Docker no se escucha en interfaces de red del ordenador.
    direccion = '0.0.0.0' if Path('/.dockerenv').is_file() else '127.0.0.1'
    with ExitStack() as pila:
        temporal = pila.enter_context(TemporaryDirectory(prefix='taxiroute-sqlite-cert-'))
        certificado, clave = certificado_temporal(temporal)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(certificado, clave)
        app = pila.enter_context(abrir_demo_sqlite(Path.home() / '.taxiroute' / 'demo-sqlite-vinculada',
            mostrar_codigo=lambda codigo: print(f'PIN ficticio de administrador: {codigo}', flush=True)))
        servidor = make_server(direccion, 8769, app, ssl_context=tls, threaded=True)
        print('DEMOSTRACIÓN: solo datos y credenciales ficticios; no usar datos reales.', flush=True)
        print('Acceso local con Compose: https://127.0.0.1:18769/', flush=True)
        def terminar(_senyal, _marco):
            # Docker envía SIGTERM; salir permite cerrar servidor y controles.
            raise SystemExit(0)
        anterior = signal.signal(signal.SIGTERM, terminar)
        try:
            servidor.serve_forever()
        finally:
            signal.signal(signal.SIGTERM, anterior)
            servidor.server_close()


if __name__ == '__main__':
    main()
