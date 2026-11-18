"""Comprobación de sesión para futuras rutas Flask; no activa protección global."""
from functools import wraps

from flask import abort, g, request


NOMBRE_COOKIE = "taxiroute_sesion"


def requerir_sesion(servicio_sesiones, *, roles):
    """Exige identidad vigente y alguno de los roles fijados al definir la ruta.

    La consulta no renueva actividad. La ruta que modifique datos deberá
    protegerse además contra solicitudes cruzadas. El servicio verifica
    las credenciales; los roles y el usuario nunca se leen del navegador.
    """
    permitidos = frozenset(roles)
    if not permitidos or not permitidos <= {"administrador", "conductor"}:
        raise ValueError("Indica uno o ambos roles permitidos.")

    def decorar(funcion):
        @wraps(funcion)
        def protegida(*args, **kwargs):
            token = request.cookies.get(NOMBRE_COOKIE)
            identidad = servicio_sesiones.consultar(token)
            if identidad is None:
                abort(401)
            if identidad.rol not in permitidos:
                abort(403)
            g.identidad_taxiroute = identidad
            return funcion(*args, **kwargs)
        return protegida
    return decorar
