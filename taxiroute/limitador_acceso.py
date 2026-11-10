"""Límite compartido de acceso para una aplicación local de un proceso."""
from collections import deque
from math import ceil, isfinite
from threading import Lock
from time import monotonic


class LimitadorAcceso:
    """Ventana deslizante acotada, sin identificar cuentas ni clientes.

    Cuenta solicitudes aceptadas, incluidas las correctas. El estado se pierde
    al reiniciar y no se comparte entre procesos; no basta para uso distribuido.
    """
    def __init__(self, *, maximo=30, periodo=60, reloj=monotonic):
        if type(maximo) is not int or not 1 <= maximo <= 1000:
            raise ValueError("El máximo debe estar entre 1 y 1000.")
        if isinstance(periodo, bool) or not isinstance(periodo, (int, float)) or not isfinite(periodo) or periodo <= 0:
            raise ValueError("El periodo debe ser positivo y finito.")
        if not callable(reloj):
            raise TypeError("Se necesita un reloj monotónico.")
        self._maximo = maximo
        self._periodo = periodo
        self._reloj = reloj
        self._intentos = deque()
        self._bloqueo = Lock()

    def esperar_segundos(self):
        """Registra el intento y devuelve 0, o segundos de espera si está lleno."""
        with self._bloqueo:
            ahora = self._reloj()
            while self._intentos and ahora - self._intentos[0] >= self._periodo:
                self._intentos.popleft()
            if len(self._intentos) >= self._maximo:
                return max(1, ceil(self._periodo - (ahora - self._intentos[0])))
            self._intentos.append(ahora)
            return 0
