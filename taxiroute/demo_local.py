"""Servidor HTTPS de demostración con cuentas y buzón desechables."""
import argparse
from pathlib import Path
from secrets import token_bytes
import ssl
from tempfile import TemporaryDirectory

from werkzeug.serving import make_server

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_demo_acceso import crear_app_demo_acceso


def crear_demo(carpeta):
    """Solo datos ficticios; nunca recibe ni toca el historial o el correo reales."""
    ruta = Path(carpeta) / "cuentas-demo.sqlite3"
    inicializar_base(ruta)
    cuenta = crear_cuenta(ruta, nombre_usuario="AdminDemo", nombre_visible="Administración de prueba",
                          contrasena="DemoTaxiroute123!", rol="administrador")
    with conectar(ruta) as conexion:
        conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id=?", (cuenta.id,))
        conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                         (cuenta.id, "demo@example.invalid"))
    buzon = []
    def entrega_ficticia(destinatario, codigo):
        if destinatario != "demo@example.invalid":
            raise ValueError("La demostración solo admite su destinatario ficticio.")
        buzon.append(codigo)
    pin = ServicioPIN(ruta, clave_hmac=token_bytes(32), enviar=entrega_ficticia)
    return crear_app_demo_acceso(pin, ServicioSesiones(ruta), buzon_demo=buzon)


def main():
    parser = argparse.ArgumentParser(description="Demostración HTTPS local con datos ficticios")
    parser.add_argument("--certificado", type=Path, required=True, help="Certificado local PEM")
    parser.add_argument("--clave", type=Path, required=True, help="Clave privada PEM")
    args = parser.parse_args()
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(args.certificado, args.clave)
    with TemporaryDirectory(prefix="taxiroute-demo-acceso-") as temporal:
        app = crear_demo(temporal)
        servidor = make_server("127.0.0.1", 8766, app, ssl_context=contexto, threaded=True)
        print("Demostración local preparada en https://127.0.0.1:8766")
        print("Datos ficticios; no se utiliza el correo real. Cierra con Ctrl+C.")
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            servidor.server_close()


if __name__ == "__main__":
    main()
