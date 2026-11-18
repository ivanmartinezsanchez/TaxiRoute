"""Servidor SQLite bajo proxy; aislado y no apto para datos reales."""
from pathlib import Path
import sys

from taxiroute.recuperar_admin_local import _Argumentos
from taxiroute.servidor_gunicorn_experimental import crear_servidor_gunicorn
from taxiroute.servidor_sqlite_local import _validar_direccion


def main(argv=None):
    parser = _Argumentos(description="Servidor de prueba local bajo proxy.")
    for nombre in ("cuentas", "historial", "tarifas", "certificado", "clave", "secreto-gmail"):
        parser.add_argument("--" + nombre, type=Path, required=True)
    parser.add_argument("--direccion", default="127.0.0.1",
                        help="IPv4 privada de la autoridad externa; no publica puertos")
    parser.add_argument("--puerto-externo", type=int, default=18771,
                        help="Puerto de la autoridad externa; no publica puertos")
    args = parser.parse_args(argv)
    if not 1 <= args.puerto_externo <= 65535:
        parser.error("El puerto externo no es válido.")
    try:
        _validar_direccion(args.direccion)
    except ValueError:
        parser.error("La dirección debe ser 127.0.0.1 o una IPv4 privada explícita.")

    def enviar(destino, codigo):
        from taxiroute.correo_gmail import EnviadorGmail
        from taxiroute.oauth_gmail_contenedor import cliente_gmail_desde_secreto
        EnviadorGmail(cliente_gmail_desde_secreto(args.secreto_gmail))(destino, codigo)

    aplicacion = dict(ruta_cuentas=args.cuentas, ruta_historial=args.historial,
        ruta_tarifas=args.tarifas, certificado=args.certificado, clave=args.clave,
        direccion=args.direccion, puerto=args.puerto_externo, enviar_pin=enviar)
    opciones = dict(escucha="127.0.0.1", puerto=18772,
        certificado=args.certificado, clave=args.clave, aplicacion=aplicacion)
    try:
        crear_servidor_gunicorn(opciones).run()
    except (ImportError, ValueError, OSError):
        print("No se pudo iniciar el servidor experimental.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
