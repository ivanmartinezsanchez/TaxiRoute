"""Servidor SQLite bajo proxy; aislado y no apto para datos reales."""
from pathlib import Path
import sys

from taxiroute.recuperar_admin_local import _Argumentos
from taxiroute.servidor_gunicorn_experimental import crear_servidor_gunicorn


def main(argv=None):
    parser = _Argumentos(description="Servidor de prueba local bajo proxy.")
    for nombre in ("cuentas", "historial", "tarifas", "certificado", "clave", "secreto-gmail"):
        parser.add_argument("--" + nombre, type=Path, required=True)
    args = parser.parse_args(argv)

    def enviar(destino, codigo):
        from taxiroute.correo_gmail import EnviadorGmail
        from taxiroute.oauth_gmail_contenedor import cliente_gmail_desde_secreto
        EnviadorGmail(cliente_gmail_desde_secreto(args.secreto_gmail))(destino, codigo)

    aplicacion = dict(ruta_cuentas=args.cuentas, ruta_historial=args.historial,
        ruta_tarifas=args.tarifas, certificado=args.certificado, clave=args.clave,
        direccion="127.0.0.1", puerto=18771, enviar_pin=enviar)
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
