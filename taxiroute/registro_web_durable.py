"""Eventos operativos mínimos para la web durable experimental."""
from pathlib import Path

from taxiroute.registro import RegistroActividad

_EVENTOS = frozenset({"aplicacion_iniciada", "aplicacion_finalizada", "carrera_iniciada",
    "estado_cambiado", "carrera_finalizada", "historial_guardado", "carrera_recuperada", "error_operacion"})
_AVISO = "Aviso: no se están pudiendo guardar los registros de actividad. Se volverá a intentar en el próximo evento."


class RegistroWebDurable:
    """Registra solo nombres de eventos y estados conocidos, sin datos privados.

    Es mejor esfuerzo: los eventos no participan en la transacción de carrera.
    Un fallo o una interrupción pueden causar huecos; no es un registro de
    auditoría íntegro. Nunca escribe en Git ni a través de enlaces simbólicos.
    """
    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self._registro = RegistroActividad(self.ruta)

    def registrar(self, evento, nivel="INFO", **datos):
        try:
            if evento.startswith("error_"):
                evento = "error_operacion"
            estado = datos.get("estado")
            if evento not in _EVENTOS or (estado is not None and estado not in {"parado", "en movimiento"}):
                return _AVISO
            carpeta = self.ruta.parent
            if (not carpeta.is_dir() or carpeta.is_symlink()
                    or any((padre / ".git").exists() or (padre / ".git").is_symlink()
                           for padre in (carpeta, *carpeta.parents))):
                return _AVISO
            archivos = [self.ruta, self.ruta.with_name(self.ruta.name + ".lock")]
            archivos.extend(self.ruta.with_name(f"{self.ruta.name}.{n}") for n in range(1, 6))
            if any(archivo.is_symlink() for archivo in archivos):
                return _AVISO
            datos = {} if estado is None else {"estado": estado}
            return self._registro.registrar(evento, nivel="ERROR" if evento == "error_operacion" else "INFO", **datos)
        except Exception:
            return _AVISO
