"""Servidor local experimental de la web CSV. No preparado para datos reales."""
from contextlib import closing, contextmanager
from pathlib import Path
from secrets import token_bytes
import sqlite3
import ssl
import sys

from werkzeug.serving import make_server

from taxiroute.composicion_csv_experimental import abrir_app_csv_experimental
from taxiroute.correo_gmail import EnviadorGmail
from taxiroute.preparar_csv_local import MARCADOR_PREPARACION
from taxiroute.recuperar_admin_local import _Argumentos


@contextmanager
def servidor_csv_experimental(*, carpeta, certificado, clave, puerto, enviar_pin):
    """Abre HTTPS en bucle local; el llamador controla su ejecución y cierre.

    El código PIN se envía solo mediante el transporte inyectado. La clave de
    verificación se cambia al reiniciar: los PIN pendientes dejan de servir.
    Los datos de carreras activas no sobreviven al apagado del proceso.
    """
    carpeta, certificado, clave = Path(carpeta), Path(certificado), Path(clave)
    if type(puerto) is not int or not 0 <= puerto <= 65535:
        raise ValueError("El puerto no es válido.")
    if carpeta.is_symlink() or not carpeta.is_dir():
        raise ValueError("La carpeta de la preparación no es válida.")
    marcador = carpeta / "PREPARACION_EXPERIMENTAL.txt"
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != MARCADOR_PREPARACION:
        raise ValueError("La carpeta no es una preparación experimental reconocida.")
    if any(ruta.is_symlink() or not ruta.is_file() for ruta in (certificado, clave)):
        raise ValueError("Se necesitan un certificado y una clave TLS existentes.")
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(certificado, clave)
    cuentas = carpeta / "cuentas.sqlite3"
    with abrir_app_csv_experimental(ruta_cuentas=cuentas, ruta_tarifas=carpeta / "tarifas.json",
            carpeta_historial=carpeta / "historiales", clave_hmac=token_bytes(32), enviar_pin=enviar_pin) as app:
        with closing(sqlite3.connect(cuentas.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conexion:
            administrador = conexion.execute("""SELECT 1 FROM usuarios AS u
                JOIN configuracion_administrador AS c ON c.usuario_id=u.id
                WHERE u.rol='administrador' AND u.habilitado=1 LIMIT 1""").fetchone()
            if administrador is None:
                raise ValueError("La preparación necesita un administrador habilitado y un correo configurado.")
        app.config["DEMO_PERSISTENTE"] = True
        servidor = make_server("127.0.0.1", puerto, app, ssl_context=contexto, threaded=True)
        try:
            yield servidor
        finally:
            servidor.server_close()


def main(argv=None):
    parser = _Argumentos(description="Servidor HTTPS local experimental con historial CSV.")
    parser.add_argument("--carpeta", type=Path, required=True, help="Carpeta experimental preparada")
    parser.add_argument("--certificado", type=Path, required=True, help="Certificado TLS PEM")
    parser.add_argument("--clave", type=Path, required=True, help="Clave privada TLS PEM")
    parser.add_argument("--puerto", type=int, default=8768, help="Puerto local (por defecto 8768)")
    argumentos = parser.parse_args(argv)
    if not 1 <= argumentos.puerto <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535.")
    try:
        # No accede a Gmail hasta que se solicita un PIN con contraseña válida.
        def enviar(destinatario, codigo):
            from taxiroute.oauth_gmail import cliente_gmail_autorizado
            EnviadorGmail(cliente_gmail_autorizado())(destinatario, codigo)

        with servidor_csv_experimental(carpeta=argumentos.carpeta, certificado=argumentos.certificado,
                clave=argumentos.clave, puerto=argumentos.puerto, enviar_pin=enviar) as servidor:
            try:
                from taxiroute.oauth_gmail import cliente_gmail_autorizado
                # Comprueba la autorización existente; no envía ningún mensaje.
                cliente_gmail_autorizado()
            except Exception:
                print("No se ha podido preparar Gmail. Revisa la autorización y la conexión a internet antes de iniciar la aplicación.", file=sys.stderr)
                return 1
            print(f"Servidor local experimental: https://127.0.0.1:{argumentos.puerto}/", flush=True)
            print("Solo datos ficticios. Al detener el proceso se puede perder una carrera activa.", flush=True)
            try:
                servidor.serve_forever()
            except KeyboardInterrupt:
                print("\nServidor detenido.", flush=True)
    except (ValueError, OSError, sqlite3.Error, ssl.SSLError):
        print("No se ha podido iniciar la aplicación. Revisa la preparación, TLS y el puerto local.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
