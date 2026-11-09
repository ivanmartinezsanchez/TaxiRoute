"""Servidor HTTPS de prueba: cuentas, correo e historial totalmente ficticios."""
import argparse
from contextlib import closing, contextmanager, ExitStack
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
    ruta_tarifas = carpeta / "tarifas.json"
    guardar_tarifas(ruta_tarifas, {"parado": "0.02", "en_movimiento": "0.05"})
    if durable:
        HistorialSQLite(carpeta / "historial.sqlite3", exigir_existente=True).inicializar()
    return _armar_demo(carpeta, mostrar_codigo=mostrar_codigo, durable=durable)


_MARCADOR_DEMO = "TaxiRoute demo persistente ficticia v1\n"


def _armar_demo(carpeta, *, mostrar_codigo, durable):
    """Monta servicios sobre datos ya inicializados, sin reescribirlos."""
    from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion

    ruta = carpeta / "cuentas.sqlite3"
    ruta_tarifas = carpeta / "tarifas.json"
    ruta_historial = carpeta / "historial.sqlite3"
    def entrega_ficticia(destinatario, codigo):
        if destinatario != "demo@example.invalid":
            raise ValueError("La demostración solo admite su buzón ficticio.")
        mostrar_codigo(codigo)
    sesiones = ServicioSesiones(ruta)
    pin = ServicioPIN(ruta, clave_hmac=token_bytes(32), enviar=entrega_ficticia)
    def historial(usuario):
        return HistorialSQLite(ruta_historial, usuario_id=usuario, exigir_existente=durable)
    fabrica = lambda usuario: ServicioCarreras(
        crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(ruta_tarifas)),
        historial=historial(usuario))
    if durable:
        # Comprobar antes de abrir rutas que las bases existen y son compatibles.
        HistorialSQLite(ruta_historial, exigir_existente=True).consultar()
        cargar_tarifas(ruta_tarifas)
        flota = crear_flota_con_recuperacion(ruta, fabrica, modo_durable_experimental=True).flota
    else:
        flota = FlotaEnMemoria(fabrica)
    app = crear_app_integrada(sesiones, pin, flota, historial, ruta_tarifas=ruta_tarifas,
                              ruta_historial_durable=ruta_historial if durable else None)
    if durable:
        from taxiroute.control_periodico import ControlPeriodico
        app.extensions["control_periodico"] = ControlPeriodico(
            flota, ruta, intervalo=5,
            avisar=lambda: print("No se pudo guardar un punto; la carrera necesita revisión.", flush=True))
    return app


@contextmanager
def demo_persistente(carpeta, *, mostrar_codigo):
    """Abre una demo ficticia exclusiva o crea una carpeta nueva para ella.

    La persona integradora debe mantener este contexto abierto todo el tiempo
    que use la aplicación. No admite una carpeta preexistente sin marcador.
    """
    from taxiroute.bloqueo import bloqueo_historial
    import sqlite3

    carpeta = Path(carpeta)
    if carpeta.is_symlink():
        raise ValueError("La carpeta de demostración no puede ser un enlace.")
    resuelta = carpeta.resolve()
    if any((padre / ".git").exists() for padre in (resuelta, *resuelta.parents)):
        raise ValueError("La carpeta persistente debe estar fuera de un repositorio Git.")
    creada = False
    try:
        carpeta.mkdir()
        creada = True
    except FileExistsError:
        if not carpeta.is_dir():
            raise ValueError("La ruta de demostración no es una carpeta.")
    marcador = carpeta / "DEMO_FICTICIA.txt"
    if not creada:
        if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != _MARCADOR_DEMO:
            raise ValueError("La carpeta no es una demostración ficticia reconocida.")
    if (carpeta / "servidor.lock").is_symlink():
        raise ValueError("El bloqueo de la demostración no puede ser un enlace.")
    with bloqueo_historial(carpeta / "servidor", espera=0):
        if creada:
            app = crear_demo_temporal(carpeta, mostrar_codigo=mostrar_codigo, durable=True)
            with marcador.open("x", encoding="utf-8", newline="") as archivo:
                archivo.write(_MARCADOR_DEMO)
        else:
            for nombre in ("cuentas.sqlite3", "historial.sqlite3", "tarifas.json"):
                ruta = carpeta / nombre
                if ruta.is_symlink() or not ruta.is_file():
                    raise ValueError("Falta un archivo de demostración o es un enlace.")
            uri = (carpeta / "cuentas.sqlite3").resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as conexion:
                cuentas = conexion.execute(
                    "SELECT u.nombre_usuario, u.rol, c.correo_verificacion "
                    "FROM usuarios u LEFT JOIN configuracion_administrador c ON c.usuario_id=u.id "
                    "WHERE u.rol='administrador'"
                ).fetchall()
                if cuentas != [("AdminDemo", "administrador", "demo@example.invalid")]:
                    raise ValueError("Las cuentas no corresponden a la demostración ficticia.")
            app = _armar_demo(carpeta, mostrar_codigo=mostrar_codigo, durable=True)
        app.config["DEMO_PERSISTENTE"] = True
        yield app



def main():
    parser = argparse.ArgumentParser(description="Demo HTTPS local de TaxiRoute con datos ficticios")
    parser.add_argument("--certificado", type=Path, required=True, help="Certificado local PEM")
    parser.add_argument("--clave", type=Path, required=True, help="Clave privada PEM")
    parser.add_argument("--puerto", type=int, default=8767, help="Puerto local de la demostración")
    parser.add_argument("--durable", action="store_true", help="Activa el protocolo experimental de guardado")
    parser.add_argument("--carpeta-demo", type=Path, help="Carpeta exclusiva para demo ficticia persistente (implica --durable)")
    argumentos = parser.parse_args()
    if not 1 <= argumentos.puerto <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535.")
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(argumentos.certificado, argumentos.clave)
    with ExitStack() as pila:
        mostrar_codigo = lambda codigo: print(f"Código ficticio de administrador: {codigo}", flush=True)
        if argumentos.carpeta_demo is not None:
            app = pila.enter_context(demo_persistente(argumentos.carpeta_demo, mostrar_codigo=mostrar_codigo))
            argumentos.durable = True
        else:
            temporal = pila.enter_context(TemporaryDirectory(prefix="taxiroute-integrada-demo-"))
            app = crear_demo_temporal(temporal, mostrar_codigo=mostrar_codigo, durable=argumentos.durable)
        servidor = make_server("127.0.0.1", argumentos.puerto, app, ssl_context=contexto, threaded=True)
        print(f"Demo local: https://127.0.0.1:{argumentos.puerto}/", flush=True)
        print("Solo datos ficticios; no se envía ningún correo.", flush=True)
        if argumentos.durable:
            print("Modo durable experimental: se intentan guardar puntos cada 5 segundos; la recuperación aún es incompleta.", flush=True)
        print(f"Conductor: AnaDemo / {CLAVE_CONDUCTOR_DEMO}", flush=True)
        print(f"Administrador: AdminDemo / {CLAVE_ADMIN_DEMO}", flush=True)
        if argumentos.carpeta_demo is None:
            print("Al detener la demo se borran sus datos temporales. Ctrl+C para detener.", flush=True)
        else:
            print(f"Los datos ficticios se conservan en: {argumentos.carpeta_demo}. Ctrl+C para detener.", flush=True)
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