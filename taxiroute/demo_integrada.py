"""Servidor HTTPS de prueba: cuentas, correo e historial totalmente ficticios."""
import argparse
from pathlib import Path
from secrets import token_bytes
import ssl
from tempfile import TemporaryDirectory

from werkzeug.serving import make_server

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas, guardar_tarifas
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


CLAVE_CONDUCTOR_DEMO = "ConductorDemo123!"
CLAVE_ADMIN_DEMO = "AdminDemo456!"


def crear_demo_temporal(carpeta, *, mostrar_codigo, durable=False):
    """Configura exclusivamente cuentas de prueba y un buzón sin correo real."""
    carpeta = Path(carpeta)
    ruta = carpeta / "cuentas.sqlite3"
    inicializar_base(ruta)
    conductor = crear_cuenta(ruta, nombre_usuario="AnaDemo", nombre_visible="Ana de prueba",
                             contrasena=CLAVE_CONDUCTOR_DEMO)
    admin = crear_cuenta(ruta, nombre_usuario="AdminDemo", nombre_visible="Administración de prueba",
                         contrasena=CLAVE_ADMIN_DEMO, rol="administrador")
    with conectar(ruta) as conexion:
        conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id IN (?, ?)",
                         (conductor.id, admin.id))
        conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                         (admin.id, "demo@example.invalid"))
    def entrega_ficticia(destinatario, codigo):
        if destinatario != "demo@example.invalid":
            raise ValueError("La demostración solo admite su buzón ficticio.")
        mostrar_codigo(codigo)
    sesiones = ServicioSesiones(ruta)
    pin = ServicioPIN(ruta, clave_hmac=token_bytes(32), enviar=entrega_ficticia)
    ruta_tarifas = carpeta / "tarifas.json"
    guardar_tarifas(ruta_tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
    def historial(usuario):
        return HistorialSQLite(carpeta / "historial.sqlite3", usuario_id=usuario)
    ruta_historial = carpeta / "historial.sqlite3"
    if durable:
        HistorialSQLite(ruta_historial, exigir_existente=True).inicializar()
    flota = FlotaEnMemoria(lambda usuario: ServicioCarreras(
        crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(ruta_tarifas)),
        historial=historial(usuario)), modo_durable_experimental=durable)
    app = crear_app_integrada(sesiones, pin, flota, historial, ruta_tarifas=ruta_tarifas,
                              ruta_historial_durable=ruta_historial if durable else None)
    if durable:
        from taxiroute.control_periodico import ControlPeriodico
        app.extensions["control_periodico"] = ControlPeriodico(
            flota, ruta, intervalo=5,
            avisar=lambda: print("No se pudo guardar un punto; la carrera necesita revisión.", flush=True))
    return app


def main():
    parser = argparse.ArgumentParser(description="Demo HTTPS local de TaxiRoute con datos temporales")
    parser.add_argument("--certificado", type=Path, required=True, help="Certificado local PEM")
    parser.add_argument("--clave", type=Path, required=True, help="Clave privada PEM")
    parser.add_argument("--puerto", type=int, default=8767, help="Puerto local de la demostración")
    parser.add_argument("--durable", action="store_true", help="Activa el protocolo experimental de guardado")
    argumentos = parser.parse_args()
    if not 1 <= argumentos.puerto <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535.")
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(argumentos.certificado, argumentos.clave)
    with TemporaryDirectory(prefix="taxiroute-integrada-demo-") as temporal:
        app = crear_demo_temporal(temporal, mostrar_codigo=lambda codigo:
                                 print(f"Código ficticio de administrador: {codigo}", flush=True),
                                 durable=argumentos.durable)
        servidor = make_server("127.0.0.1", argumentos.puerto, app, ssl_context=contexto, threaded=True)
        print(f"Demo local: https://127.0.0.1:{argumentos.puerto}/", flush=True)
        print("Solo datos ficticios y temporales; no se envía ningún correo.", flush=True)
        if argumentos.durable:
            print("Modo durable experimental: se intentan guardar puntos cada 5 segundos; la recuperación aún es incompleta.", flush=True)
        print(f"Conductor: AnaDemo / {CLAVE_CONDUCTOR_DEMO}", flush=True)
        print(f"Administrador: AdminDemo / {CLAVE_ADMIN_DEMO}", flush=True)
        print("Al detener la demo se borran sus datos temporales. Ctrl+C para detener.", flush=True)
        control = app.extensions.get("control_periodico")
        if control is not None:
            control.iniciar()
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            servidor.server_close()
            if control is not None:
                control.detener()


if __name__ == "__main__":
    main()