"""Demostración HTTPS local con CSV y cuentas exclusivamente ficticias.

El certificado es autofirmado, el PIN se imprime en consola y no se envía
correo. No es una instalación de producción ni debe usarse con datos reales.
"""
import argparse
from contextlib import closing, contextmanager, ExitStack
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from taxiroute.base_cuentas import conectar
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_integrada import CLAVE_ADMIN_DEMO, CLAVE_CONDUCTOR_DEMO
from taxiroute.inicializacion_administrador import preparar_primer_administrador
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.servidor_csv_local import servidor_csv_experimental


MARCADOR = "TaxiRoute: demostración CSV ficticia, versión 1.\n"
ARCHIVO_MARCADOR = "DEMO_CSV_FICTICIA.txt"
CORREO_FICTICIO = "demo@example.invalid"


def preparar_demo_csv(carpeta):
    """Crea una demo nueva o valida una ya identificada, sin sobrescribir.

    Una creación incompleta no se elimina y no se reconoce como demo terminada.
    La validación no protege contra manipulación local deliberada de archivos.
    """
    destino = Path(carpeta).expanduser()
    if destino.is_symlink():
        raise ValueError("La carpeta de demostración no puede ser un enlace.")
    if not destino.exists():
        archivos = preparar_archivos_csv(destino)
        admin = preparar_primer_administrador(archivos.cuentas, nombre_usuario="AdminDemo",
            nombre_visible="Administración de prueba", contrasena=CLAVE_ADMIN_DEMO, correo=CORREO_FICTICIO)
        conductor = crear_cuenta(archivos.cuentas, nombre_usuario="AnaDemo",
            nombre_visible="Ana de prueba", contrasena=CLAVE_CONDUCTOR_DEMO)
        with conectar(archivos.cuentas) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id IN (?, ?)",
                                 (admin.id, conductor.id))
                conexion.commit()
            except BaseException:
                conexion.rollback()
                raise
        with (archivos.carpeta / ARCHIVO_MARCADOR).open("x", encoding="utf-8", newline="\n") as archivo:
            archivo.write(MARCADOR)
        return archivos.carpeta

    if not destino.is_dir():
        raise ValueError("La ruta de demostración no es una carpeta.")
    resuelta = destino.resolve()
    if any((padre / ".git").exists() or (padre / ".git").is_symlink() for padre in (resuelta, *resuelta.parents)):
        raise ValueError("La demostración debe estar fuera de un repositorio Git.")
    marcador = destino / ARCHIVO_MARCADOR
    if marcador.is_symlink() or not marcador.is_file() or marcador.read_text(encoding="utf-8") != MARCADOR:
        raise ValueError("La carpeta no es una demostración CSV reconocida.")
    for nombre in ("cuentas.sqlite3", "tarifas.json", "PREPARACION_EXPERIMENTAL.txt"):
        ruta = destino / nombre
        if ruta.is_symlink() or not ruta.is_file():
            raise ValueError("La demostración tiene archivos ausentes o enlaces.")
    historiales = destino / "historiales"
    if historiales.is_symlink() or not historiales.is_dir():
        raise ValueError("La carpeta de historiales no está disponible.")
    with closing(sqlite3.connect((destino / "cuentas.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)) as conexion:
        administradores = conexion.execute("""SELECT u.nombre_usuario, c.correo_verificacion FROM usuarios u
            LEFT JOIN configuracion_administrador c ON c.usuario_id=u.id WHERE u.rol='administrador'""").fetchall()
        if administradores != [("AdminDemo", CORREO_FICTICIO)]:
            raise ValueError("La cuenta administradora no corresponde a la demostración.")
    return resuelta


@contextmanager
def servidor_demo_csv(carpeta, certificado, clave, *, puerto=8768, mostrar_codigo=print):
    """Abre el servidor de prueba solo en 127.0.0.1 con recuperación opcional activa."""
    if not callable(mostrar_codigo):
        raise TypeError("Se necesita una función para mostrar el PIN ficticio.")
    carpeta = preparar_demo_csv(carpeta)
    def enviar_simulado(destinatario, codigo):
        if destinatario != CORREO_FICTICIO:
            raise ValueError("La demostración solo admite su buzón ficticio.")
        mostrar_codigo(codigo)
    with servidor_csv_experimental(carpeta=carpeta, certificado=certificado, clave=clave,
                                  puerto=puerto, enviar_pin=enviar_simulado, durable=True) as servidor:
        servidor.app.config["PIN_DEMO_TERMINAL"] = True
        yield servidor


def main(argv=None):
    parser = argparse.ArgumentParser(description="Demo CSV protegida local con datos exclusivamente ficticios")
    parser.add_argument("--carpeta-demo", type=Path,
                        help="Carpeta nueva fuera de Git o demo CSV ficticia ya creada")
    parser.add_argument("--puerto", type=int, default=8768, help="Puerto local (por defecto 8768)")
    args = parser.parse_args(argv)
    if not 1 <= args.puerto <= 65535:
        parser.error("El puerto debe estar entre 1 y 65535.")
    try:
        with ExitStack() as pila:
            certificados = Path(pila.enter_context(TemporaryDirectory(prefix="taxiroute-cert-demo-")))
            certificado, clave = certificado_temporal(certificados)
            if args.carpeta_demo is None:
                temporal = Path(pila.enter_context(TemporaryDirectory(prefix="taxiroute-demo-csv-")))
                carpeta = temporal / "datos"
            else:
                carpeta = args.carpeta_demo
            servidor = pila.enter_context(servidor_demo_csv(carpeta, certificado, clave, puerto=args.puerto,
                mostrar_codigo=lambda codigo: print(f"PIN ficticio del administrador: {codigo}", flush=True)))
            print(f"Demo ficticia local: https://127.0.0.1:{args.puerto}/", flush=True)
            print("El certificado es autofirmado: el navegador puede mostrar una advertencia.", flush=True)
            print("No se envía correo ni deben usarse datos reales. No es para producción.", flush=True)
            print(f"Conductor ficticio: AnaDemo / {CLAVE_CONDUCTOR_DEMO}", flush=True)
            print(f"Administrador ficticio: AdminDemo / {CLAVE_ADMIN_DEMO}", flush=True)
            print("Datos temporales: se borran al cerrar." if args.carpeta_demo is None else
                  f"Datos ficticios persistentes en: {carpeta}", flush=True)
            try:
                servidor.serve_forever()
            except KeyboardInterrupt:
                print("\nDemostración detenida.", flush=True)
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print("No se ha podido abrir la demostración. Comprueba la carpeta y el puerto.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
