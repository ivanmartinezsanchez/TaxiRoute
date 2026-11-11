"""Servidor HTTPS local experimental con historial SQLite vinculado.

Solo para pruebas: el transporte de PIN se inyecta y no se inicializan cuentas,
credenciales ni certificados. No es un servidor de producción.
"""
from contextlib import closing, contextmanager
from pathlib import Path
from secrets import token_bytes
import ssl

from werkzeug.serving import make_server

from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado
from taxiroute.vinculo_historial import _registro


@contextmanager
def servidor_sqlite_experimental(*, ruta_cuentas, ruta_historial, ruta_tarifas,
                                 certificado, clave, puerto, enviar_pin):
    """Mantiene el bloqueo y control durable mientras el servidor esté abierto.

    Exige una instalación SQLite ya vinculada. Nunca consulta Gmail ni envía
    por sí mismo: el integrador proporciona el transporte de PIN. La clave de
    verificación cambia en cada arranque, invalidando desafíos anteriores.
    """
    if type(puerto) is not int or not 0 <= puerto <= 65535:
        raise ValueError("El puerto no es válido.")
    if not callable(enviar_pin):
        raise ValueError("Se necesita un transporte de PIN.")
    cuentas, historial, tarifas, cert, llave = map(
        Path, (ruta_cuentas, ruta_historial, ruta_tarifas, certificado, clave))
    if any(ruta.is_symlink() or not ruta.is_file()
           for ruta in (cuentas, historial, tarifas, cert, llave)):
        raise ValueError("Se necesitan cuentas, historial, tarifas y archivos TLS existentes.")
    # Rechazar antes del arranque un modo antiguo sin vínculo; repetir la
    # verificación dentro del contexto que conserva el bloqueo cooperativo.
    with closing(_conectar_existente(cuentas)) as conexion:
        vinculo = _registro(conexion)
        if vinculo is None or vinculo['formato'] != 'sqlite' or vinculo['estado'] != 'listo':
            raise ValueError("Se necesita un historial SQLite vinculado y listo.")
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(cert, llave)
    with abrir_app_experimental(ruta_cuentas=cuentas, ruta_historial=historial,
            ruta_tarifas=tarifas, clave_hmac=token_bytes(32), enviar_pin=enviar_pin) as app:
        verificar_historial_sqlite_vinculado(cuentas, historial)
        with closing(_conectar_existente(cuentas)) as conexion:
            admin = conexion.execute("""SELECT 1 FROM usuarios u
                JOIN configuracion_administrador c ON c.usuario_id=u.id
                WHERE u.rol='administrador' AND u.habilitado=1 LIMIT 1""").fetchone()
            if admin is None:
                raise ValueError("La instalación necesita un administrador habilitado.")
        servidor = make_server('127.0.0.1', puerto, app, ssl_context=contexto, threaded=True)
        try:
            yield servidor
        finally:
            servidor.server_close()
