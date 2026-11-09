"""Composición interna de la web experimental sobre archivos existentes.

No crea cuentas, no busca credenciales, no inicia servidores y no prepara una
instalación de producción. El integrador debe mantener la exclusión entre
procesos, proporcionar HTTPS y gestionar el ciclo del control periódico.
"""
from pathlib import Path

from taxiroute.acceso import _ahora
from taxiroute.arranque_recuperacion import crear_flota_con_recuperacion
from taxiroute.carrera import Carrera
from taxiroute.configuracion import cargar_tarifas
from taxiroute.control_periodico import ControlPeriodico
from taxiroute.flota import FlotaEnMemoria
from taxiroute.historial_sqlite import HistorialSQLite
from taxiroute.pin import ServicioPIN
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import ServicioSesiones
from taxiroute.web_integrada import crear_app_integrada


def crear_app_configurada(*, ruta_cuentas, ruta_historial, ruta_tarifas, clave_hmac,
                          enviar_pin, durable, reloj=_ahora, avisar=None):
    """Monta componentes existentes; el transporte de PIN se inyecta explícitamente.

    En modo durable, el control periódico se entrega sin iniciar en
    app.extensions['control_periodico']. El llamador debe iniciar y detenerlo
    durante el ciclo de vida del servidor, y asegurar una única instancia.
    Una clave HMAC nueva invalida PIN pendientes de una ejecución anterior.
    """
    if type(durable) is not bool:
        raise ValueError("El modo durable debe ser verdadero o falso.")
    rutas = [Path(ruta_cuentas), Path(ruta_historial), Path(ruta_tarifas)]
    if any(ruta.is_symlink() or not ruta.is_file() for ruta in rutas):
        raise ValueError("Las cuentas, el historial y las tarifas deben existir como archivos.")
    if len({ruta.resolve() for ruta in rutas}) != len(rutas):
        raise ValueError("Las cuentas, el historial y las tarifas deben ser archivos distintos.")
    cuentas, historial_ruta, tarifas = rutas
    # Validar antes de construir servicios; no crear archivos ausentes.
    HistorialSQLite(historial_ruta, exigir_existente=True).consultar()
    cargar_tarifas(tarifas)
    sesiones = ServicioSesiones(cuentas, reloj=reloj)
    pin = ServicioPIN(cuentas, clave_hmac=clave_hmac, enviar=enviar_pin, reloj=reloj)

    def historial(usuario):
        return HistorialSQLite(historial_ruta, usuario_id=usuario, exigir_existente=True)

    def servicio(usuario):
        return ServicioCarreras(crear_carrera=lambda: Carrera(tarifas=cargar_tarifas(tarifas)),
                               historial=historial(usuario))

    # También valida el esquema y los pendientes antes de cualquier modo.
    preparada = crear_flota_con_recuperacion(cuentas, servicio, modo_durable_experimental=durable)
    if not durable and (preparada.pendientes or preparada.intenciones):
        raise ValueError("Hay operaciones pendientes; no se puede iniciar el modo sin recuperación.")
    flota = preparada.flota if durable else FlotaEnMemoria(servicio)
    app = crear_app_integrada(sesiones, pin, flota, historial, ruta_tarifas=tarifas,
        ruta_historial_durable=historial_ruta if durable else None, ruta_historial_flota=historial_ruta)
    if durable:
        app.extensions["control_periodico"] = ControlPeriodico(flota, cuentas, intervalo=5, avisar=avisar)
    return app
