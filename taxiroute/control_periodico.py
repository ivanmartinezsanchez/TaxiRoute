"""Puntos periódicos experimentales para una única flota en un proceso.

No recupera carreras ni sirve para datos reales. Un intervalo es un objetivo,
no una garantía ante caídas, fallos de disco o retrasos del sistema.
"""
from contextlib import closing
from math import isfinite
from threading import Event, Thread

from taxiroute.conciliacion_csv import _conectar_existente
from taxiroute.flota import FlotaBloqueada
from taxiroute.intenciones_operacion import _validar_base
from taxiroute.puntos_control import _guardar_punto_en_transaccion, punto_de_captura


class ControlPeriodico:
    def __init__(self, flota, ruta_base, *, intervalo=5, avisar=None):
        if not flota._modo_durable_experimental:
            raise ValueError("Se necesita la flota durable experimental.")
        if not isinstance(intervalo, (int, float)) or isinstance(intervalo, bool) or not isfinite(intervalo) or intervalo <= 0:
            raise ValueError("El intervalo debe ser positivo.")
        self.flota = flota
        self.ruta_base = ruta_base
        self.intervalo = intervalo
        self.avisar = avisar or (lambda: None)
        self._parar = Event()
        self._hilo = None

    def _guardar(self, usuario):
        with closing(_conectar_existente(self.ruta_base)) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            try:
                _validar_base(conexion)
                with self.flota._bloqueo:
                    if usuario in self.flota._bloqueados:
                        raise FlotaBloqueada("La carrera ya está bloqueada.")
                    servicio = self.flota._servicios.get(usuario)
                    if servicio is None or servicio.carrera is None:
                        conexion.rollback()
                        return
                    punto = punto_de_captura(servicio.carrera.capturar(), usuario,
                                               self.flota._revisiones[usuario])
                    _guardar_punto_en_transaccion(conexion, punto)
                    conexion.commit()
            except BaseException:
                conexion.rollback()
                raise

    def ciclo(self):
        """Intenta guardar carreras activas; bloquea una cuenta si falla su punto."""
        with self.flota._bloqueo:
            usuarios = [usuario for usuario, servicio in self.flota._servicios.items()
                        if servicio.carrera is not None and usuario not in self.flota._bloqueados]
        fallos = 0
        for usuario in usuarios:
            try:
                self._guardar(usuario)
            except FlotaBloqueada:
                continue
            except Exception:
                with self.flota._bloqueo:
                    servicio = self.flota._servicios.get(usuario)
                    if servicio is not None and servicio.carrera is not None:
                        self.flota._bloqueados.add(usuario)
                fallos += 1
                try:
                    self.avisar()
                except Exception:
                    # El aviso no debe impedir el control de otras carreras.
                    pass
        return fallos

    def iniciar(self):
        if self._hilo is not None:
            raise RuntimeError("El control periódico ya se inició.")
        def ejecutar():
            while not self._parar.wait(self.intervalo):
                self.ciclo()
        self._hilo = Thread(target=ejecutar, name="taxiroute-puntos", daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join()
