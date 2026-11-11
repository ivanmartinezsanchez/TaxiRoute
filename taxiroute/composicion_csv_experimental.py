"""Composición local experimental de fase 3 con un historial CSV por cuenta.

No migra historiales ni configura TLS. El modo durable es opcional y se
reserva para pruebas con datos ficticios; no está preparado para uso real.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from taxiroute.acceso import _ahora
from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.bloqueo import bloqueo_historial
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas
from taxiroute.control_periodico import ControlPeriodico
from taxiroute.flota import FlotaEnMemoria
from taxiroute.rutas_csv import _historial_de_cuenta
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada
from taxiroute.vinculo_historial import verificar_vinculo_csv
from taxiroute.registro_web_durable import RegistroWebDurable


@contextmanager
def abrir_app_csv_experimental(*, ruta_cuentas, carpeta_historial, ruta_tarifas,
                               clave_hmac, enviar_pin, reloj=_ahora, durable=False):
    """Abre una web CSV con bloqueo cooperativo mientras dure el contexto.

    Exige archivos existentes y un vínculo válido para el modo durable.
    No inicia servidor ni crea cuentas, y no está preparada para datos reales.
    """
    if type(durable) is not bool:
        raise ValueError("El modo durable debe ser verdadero o falso.")
    cuentas, tarifas, carpeta = Path(ruta_cuentas), Path(ruta_tarifas), Path(carpeta_historial)
    if (cuentas.is_symlink() or not cuentas.is_file() or tarifas.is_symlink() or not tarifas.is_file()
            or carpeta.is_symlink() or not carpeta.is_dir() or cuentas.resolve() == tarifas.resolve()):
        raise ValueError("Se necesitan cuentas, tarifas y carpeta de historial existentes y válidas.")
    cuentas, tarifas, carpeta = cuentas.resolve(), tarifas.resolve(), carpeta.resolve()
    for directorio in (cuentas.parent, carpeta):
        if any((padre / ".git").exists() or (padre / ".git").is_symlink()
               for padre in (directorio, *directorio.parents)):
            raise ValueError("Las cuentas y los historiales deben estar fuera de un repositorio Git.")
    if (cuentas.parent / "servidor.lock").is_symlink():
        raise ValueError("El bloqueo de la aplicación no puede ser un enlace.")
    with bloqueo_historial(cuentas.parent / "servidor", espera=0):
        vinculado = verificar_vinculo_csv(cuentas, carpeta)
        if durable and not vinculado:
            raise ValueError("La recuperación CSV exige una instalación vinculada.")
        cargar_tarifas(tarifas)
        sesiones = ServicioSesiones(cuentas, reloj=reloj)
        pin = ServicioPIN(cuentas, clave_hmac=clave_hmac, enviar=enviar_pin, reloj=reloj)
        registro = RegistroWebDurable(cuentas.parent / "actividad.jsonl")

        def historial(usuario):
            return _historial_de_cuenta(carpeta, usuario)

        def servicio(usuario):
            return ServicioCarreras(crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(tarifas)),
                                    historial=historial(usuario), registro=None if durable else registro)

        # Validar la identidad, el esquema y todos los registros pendientes.
        preparada = crear_flota_con_recuperacion(cuentas, servicio, modo_durable_experimental=durable)
        if not durable:
            with closing(sqlite3.connect(cuentas.as_uri() + "?mode=ro", uri=True, timeout=5)) as conexion:
                for tabla in ("carreras_recuperacion", "intenciones_operacion", "recibos_operacion"):
                    if conexion.execute(f"SELECT 1 FROM {tabla} LIMIT 1").fetchone():
                        raise ValueError("La base contiene registros durables; no se puede abrir en modo CSV.")
            app = crear_app_integrada(sesiones, pin, FlotaEnMemoria(servicio), historial, ruta_tarifas=tarifas)
            registro.registrar("aplicacion_iniciada")
            try:
                yield app
            finally:
                registro.registrar("aplicacion_finalizada")
            return
        from taxiroute.verificar_arranque_csv import verificar_historiales_guardados_csv
        verificar_historiales_guardados_csv(cuentas, carpeta)
        flota = preparada.flota
        app = crear_app_integrada(sesiones, pin, flota, historial, ruta_tarifas=tarifas,
            ruta_historial_durable=carpeta, formato_historial_durable="csv", registro_actividad=registro,
            carpeta_historial_flota_csv=carpeta)
        app.config["DEMO_PERSISTENTE"] = True
        control = ControlPeriodico(flota, cuentas, intervalo=5, avisar=lambda: registro.registrar("error_operacion"))
        app.extensions["control_periodico"] = control
        control.iniciar()
        registro.registrar("aplicacion_iniciada")
        try:
            yield app
        finally:
            try:
                control.detener()
            finally:
                registro.registrar("aplicacion_finalizada")
