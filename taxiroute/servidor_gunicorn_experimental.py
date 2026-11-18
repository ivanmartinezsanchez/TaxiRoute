"""Prototipo Gunicorn aislado para SQLite; no integrado ni para producción.

La flota y las revisiones viven en memoria: se admite exactamente un worker.
El contexto, el bloqueo y el control periódico se crean después del fork y
permanecen en el worker durante toda su vida. SIGKILL no ejecuta limpieza;
la recuperación debe revisarse tras cualquier interrupción inesperada.
"""
from contextlib import closing, contextmanager
from pathlib import Path
from secrets import token_bytes
import ssl
import sys

from flask import abort, request

from taxiroute.composicion_experimental import abrir_app_experimental
from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.recuperar_admin_local import _Argumentos
from taxiroute.servidor_sqlite_local import _validar_certificado_lan, _validar_direccion
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado
from taxiroute.vinculo_historial import _registro


@contextmanager
def abrir_app_gunicorn(*, ruta_cuentas, ruta_historial, ruta_tarifas, certificado, clave,
                       puerto, enviar_pin, direccion="127.0.0.1"):
    """Construye la aplicación bajo bloqueo; debe llamarse dentro del worker."""
    ip = _validar_direccion(direccion)
    if type(puerto) is not int or not 1 <= puerto <= 65535 or not callable(enviar_pin):
        raise ValueError("Configuración de servidor no válida.")
    cuentas, historial, tarifas, cert, llave = map(Path,
        (ruta_cuentas, ruta_historial, ruta_tarifas, certificado, clave))
    if any(p.is_symlink() or not p.is_file() for p in (cuentas, historial, tarifas, cert, llave)):
        raise ValueError("Se necesitan datos y certificados existentes.")
    _validar_certificado_lan(cert, ip)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert, llave)
    with closing(_conectar_existente(cuentas)) as conexion:
        vinculo = _registro(conexion)
        if vinculo is None or vinculo["formato"] != "sqlite" or vinculo["estado"] != "listo":
            raise ValueError("Se necesita un historial SQLite vinculado y listo.")
    with abrir_app_experimental(ruta_cuentas=cuentas, ruta_historial=historial,
            ruta_tarifas=tarifas, clave_hmac=token_bytes(32), enviar_pin=enviar_pin) as app:
        verificar_historial_sqlite_vinculado(cuentas, historial)
        with closing(_conectar_existente(cuentas)) as conexion:
            if conexion.execute("""SELECT 1 FROM usuarios u JOIN configuracion_administrador c
                    ON c.usuario_id=u.id WHERE u.rol='administrador' AND u.habilitado=1 LIMIT 1""").fetchone() is None:
                raise ValueError("Se necesita un administrador habilitado.")
        app.config["TRUSTED_HOSTS"] = [direccion]
        autoridades = {f"{direccion}:{puerto}"}
        if puerto == 443:
            autoridades.add(direccion)

        @app.before_request
        def comprobar_autoridad():
            if request.host not in autoridades:
                abort(400)

        yield app


def crear_servidor_gunicorn(opciones):
    """Construye Gunicorn sin abrir la app ni iniciar hilos en el proceso padre."""
    from gunicorn.app.base import BaseApplication

    class Servidor(BaseApplication):
        def __init__(self, configuracion):
            self._opciones = configuracion
            self._contexto = None
            super().__init__()

        def _salida_worker(self, _servidor, _worker):
            contexto, self._contexto = self._contexto, None
            if contexto is not None:
                contexto.__exit__(None, None, None)

        @staticmethod
        def _tls(_config, fabrica):
            contexto = fabrica()
            contexto.minimum_version = ssl.TLSVersion.TLSv1_2
            return contexto

        @staticmethod
        def _numero_workers(_servidor, nuevo, _anterior):
            if nuevo != 1:
                raise RuntimeError("Este prototipo solo admite un worker.")

        def load_config(self):
            parametros = {"bind": f"{self._opciones['escucha']}:{self._opciones['puerto']}",
                "workers": 1, "worker_class": "gthread", "threads": 4,
                "preload_app": False, "reload": False, "max_requests": 0,
                "timeout": 60, "graceful_timeout": 20, "accesslog": None,
                "certfile": str(self._opciones["certificado"]), "keyfile": str(self._opciones["clave"]),
                "secure_scheme_headers": {}, "ssl_context": self._tls,
                "worker_exit": self._salida_worker, "nworkers_changed": self._numero_workers}
            for nombre, valor in parametros.items():
                self.cfg.set(nombre, valor)

        def load(self):
            # Gunicorn invoca load dentro del worker si preload_app=False.
            if self._contexto is not None:
                raise RuntimeError("El worker ya abrió la instalación.")
            contexto = abrir_app_gunicorn(**self._opciones["aplicacion"])
            try:
                aplicacion = contexto.__enter__()
            except BaseException:
                self._contexto = None
                raise
            self._contexto = contexto
            return aplicacion

    return Servidor(opciones)


def main(argv=None):
    parser = _Argumentos(description="Prototipo Gunicorn local: solo datos ficticios.")
    parser.add_argument("--cuentas", type=Path, required=True)
    parser.add_argument("--historial", type=Path, required=True)
    parser.add_argument("--tarifas", type=Path, required=True)
    parser.add_argument("--certificado", type=Path, required=True)
    parser.add_argument("--clave", type=Path, required=True)
    parser.add_argument("--secreto-gmail", type=Path, required=True)
    parser.add_argument("--puerto", type=int, default=18772)
    parser.add_argument("--escucha-contenedor", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.puerto <= 65535:
        parser.error("Puerto no válido.")
    def enviar(destino, codigo):
        from taxiroute.correo_gmail import EnviadorGmail
        from taxiroute.oauth_gmail_contenedor import cliente_gmail_desde_secreto
        EnviadorGmail(cliente_gmail_desde_secreto(args.secreto_gmail))(destino, codigo)
    aplicacion = dict(ruta_cuentas=args.cuentas, ruta_historial=args.historial, ruta_tarifas=args.tarifas,
        certificado=args.certificado, clave=args.clave, puerto=args.puerto, enviar_pin=enviar)
    opciones = dict(escucha="0.0.0.0" if args.escucha_contenedor else "127.0.0.1",
        puerto=args.puerto, certificado=args.certificado, clave=args.clave, aplicacion=aplicacion)
    try:
        crear_servidor_gunicorn(opciones).run()
    except (ImportError, ValueError, OSError):
        print("No se pudo iniciar el prototipo. Revisa dependencias y archivos.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
