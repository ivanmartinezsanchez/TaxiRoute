"""Registro local de actividad en JSON Lines."""

from datetime import datetime, timezone
import json
from pathlib import Path

from taxiroute.bloqueo import bloqueo_historial


class RegistroActividad:
    """Añade eventos sin impedir la operación si el archivo falla."""

    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self._fallando = False

    def registrar(self, evento, nivel="INFO", **datos):
        """Devuelve un aviso si el registro falla o se recupera."""
        try:
            entrada = {"fecha": datetime.now(timezone.utc).isoformat(),
                       "nivel": nivel, "evento": evento, **datos}
            linea = json.dumps(entrada, ensure_ascii=False, separators=(",", ":")) + "\n"
            with bloqueo_historial(self.ruta, espera=0.1):
                if self.ruta.exists() and self.ruta.stat().st_size:
                    with self.ruta.open("rb") as anterior:
                        anterior.seek(-1, 2)
                        if anterior.read(1) != b"\n":
                            raise ValueError("El registro termina con una línea incompleta")
                with self.ruta.open("a", encoding="utf-8", newline="") as archivo:
                    archivo.write(linea)
        except Exception:
            self._fallando = True
            return ("Aviso: no se están pudiendo guardar los registros de actividad. "
                    "Se volverá a intentar en el próximo evento.")
        if self._fallando:
            self._fallando = False
            return ("El registro de actividad vuelve a estar disponible. "
                    "Los eventos que fallaron no quedaron registrados.")
        return None
