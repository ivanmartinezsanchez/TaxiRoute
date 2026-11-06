"""Almacenamiento local de carreras en CSV."""

import csv
from pathlib import Path
from taxiroute.bloqueo import bloqueo_historial

CAMPOS = ("identificador", "inicio", "fin", "duracion_segundos", "segundos_parado",
          "segundos_movimiento", "tarifa_parado", "tarifa_movimiento", "importe")


def _fila(resumen):
    datos = {campo: str(getattr(resumen, campo)) for campo in CAMPOS}
    datos["inicio"] = resumen.inicio.isoformat()
    datos["fin"] = resumen.fin.isoformat()
    return datos


def _escribir(archivo, resumen, cabecera):
    escritor = csv.DictWriter(archivo, fieldnames=CAMPOS, lineterminator="\n")
    if cabecera:
        escritor.writeheader()
    escritor.writerow(_fila(resumen))


class HistorialCSV:
    def __init__(self, ruta):
        self.ruta = Path(ruta)

    def consultar(self):
        with bloqueo_historial(self.ruta):
            return self._consultar_sin_bloqueo()

    def _consultar_sin_bloqueo(self):
        try:
            with self.ruta.open("rb") as archivo:
                archivo.seek(0, 2)
                if archivo.tell() == 0:
                    raise ValueError("El historial está vacío o incompleto")
                archivo.seek(-1, 2)
                if archivo.read(1) != b"\n":
                    raise ValueError("El historial termina con una fila incompleta")
            with self.ruta.open("r", encoding="utf-8", newline="") as archivo:
                lector = csv.DictReader(archivo, strict=True)
                if lector.fieldnames != list(CAMPOS):
                    raise ValueError("El historial tiene una cabecera incorrecta o incompleta")
                datos = list(lector)
                if any(None in fila or any(not fila[campo] for campo in CAMPOS) for fila in datos):
                    raise ValueError("El historial contiene una fila incompleta")
                return datos
        except FileNotFoundError:
            return []
        except (csv.Error, UnicodeError) as error:
            raise ValueError("El historial contiene datos que no se pueden leer") from error

    def guardar(self, resumen):
        with bloqueo_historial(self.ruta):
            self._guardar_sin_bloqueo(resumen)

    def _guardar_sin_bloqueo(self, resumen):
        registros = self._consultar_sin_bloqueo()
        fila = _fila(resumen)
        for registro in registros:
            if registro["identificador"] == fila["identificador"]:
                if registro != fila:
                    raise ValueError("La carrera ya existe con datos diferentes")
                return
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not self.ruta.exists()
        if not nuevo:
            with self.ruta.open("rb") as archivo:
                archivo.seek(-1, 2)
                if archivo.read(1) != b"\n":
                    raise ValueError("El historial termina con una fila incompleta")
        with self.ruta.open("a", encoding="utf-8", newline="") as archivo:
            _escribir(archivo, resumen, nuevo)


def exportar_copia(resumen, ruta):
    """Crea una copia nueva, sin sobrescribir archivos existentes."""
    destino = Path(ruta).expanduser()
    with destino.open("x", encoding="utf-8", newline="") as archivo:
        _escribir(archivo, resumen, True)
    return destino
