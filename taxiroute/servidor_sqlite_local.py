"""Servidor HTTPS experimental con historial SQLite vinculado.

Por defecto escucha solo en localhost. La red privada requiere una dirección
explícita y TLS configurado por el integrador. El transporte de PIN se inyecta;
no se inicializan cuentas, credenciales ni certificados. No es producción.
"""
from contextlib import closing, contextmanager
from ipaddress import IPv4Address, ip_network
from pathlib import Path
from secrets import token_bytes
import ssl

from cryptography import x509
from flask import abort, request
from werkzeug.serving import make_server

from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado
from taxiroute.vinculo_historial import _registro


_REDES_PRIVADAS = tuple(ip_network(red) for red in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def _validar_direccion(direccion):
    if not isinstance(direccion, str):
        raise ValueError("La dirección debe ser una IPv4 explícita.")
    try:
        ip = IPv4Address(direccion)
    except ValueError:
        raise ValueError("La dirección debe ser una IPv4 explícita.") from None
    if str(ip) != direccion or (direccion != '127.0.0.1' and not any(ip in red for red in _REDES_PRIVADAS)):
        raise ValueError("Solo se admite 127.0.0.1 o una dirección IPv4 privada explícita.")
    return ip


def _validar_certificado_lan(certificado, ip):
    """Comprueba el SAN, no la confianza del certificado en otros dispositivos."""
    try:
        certificado = x509.load_pem_x509_certificate(Path(certificado).read_bytes())
        nombres = certificado.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ip not in nombres.get_values_for_type(x509.IPAddress):
            raise ValueError("El certificado no incluye la dirección de red elegida.")
    except x509.ExtensionNotFound:
        raise ValueError("El certificado necesita la dirección de red en su SAN.") from None


@contextmanager
def servidor_sqlite_experimental(*, ruta_cuentas, ruta_historial, ruta_tarifas,
                                 certificado, clave, puerto, enviar_pin, direccion='127.0.0.1',
                                 escucha_contenedor=False):
    """Mantiene el bloqueo y control durable mientras el servidor esté abierto.

    Exige una instalación SQLite ya vinculada. Nunca consulta Gmail ni envía
    por sí mismo: el integrador proporciona el transporte de PIN. La clave de
    verificación cambia en cada arranque, invalidando desafíos anteriores.
    El modo LAN no establece confianza del certificado ni configura cortafuegos.
    escucha_contenedor separa el enlace interno de la autoridad LAN validada;
    quien despliega debe limitar la publicación del puerto y la red del contenedor.
    """
    if type(puerto) is not int or not 0 <= puerto <= 65535:
        raise ValueError("El puerto no es válido.")
    ip = _validar_direccion(direccion)
    modo_red = direccion != '127.0.0.1'
    if type(escucha_contenedor) is not bool or (escucha_contenedor and not modo_red):
        raise ValueError('La escucha de contenedor requiere una IPv4 privada explícita.')
    if modo_red and puerto == 0:
        raise ValueError("El acceso de red necesita un puerto explícito.")
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
    if modo_red:
        _validar_certificado_lan(cert, ip)
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
        if modo_red:
            # TRUSTED_HOSTS comprueba el host, pero Werkzeug ignora el puerto.
            app.config['TRUSTED_HOSTS'] = [direccion]
            autoridades = {f'{direccion}:{puerto}'}
            if puerto == 443:
                autoridades.add(direccion)

            @app.before_request
            def comprobar_autoridad_red():
                if request.host not in autoridades:
                    abort(400)

        direccion_escucha = '0.0.0.0' if escucha_contenedor else direccion
        servidor = make_server(direccion_escucha, puerto, app, ssl_context=contexto, threaded=True)
        try:
            yield servidor
        finally:
            servidor.server_close()
