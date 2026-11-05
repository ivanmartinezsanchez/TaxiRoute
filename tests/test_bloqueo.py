"""Pruebas con procesos independientes y archivos temporales."""

import multiprocessing
from pathlib import Path
from queue import Empty
from tempfile import TemporaryDirectory
import unittest

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.carrera import Carrera
from taxiroute.historial import HistorialCSV
from taxiroute.terminal import GestorTerminal


def guardar_en_proceso(ruta, resumen, comienzo, resultado):
    try:
        if not comienzo.wait(10):
            raise TimeoutError("No llegó la señal de comienzo")
        HistorialCSV(ruta).guardar(resumen)
        resultado.put("ok")
    except Exception as error:
        resultado.put(f"{type(error).__name__}: {error}")


def mantener_bloqueo(ruta, preparado, terminar):
    with bloqueo_historial(Path(ruta)):
        preparado.set()
        terminar.wait(15)


class PruebasBloqueo(unittest.TestCase):
    def test_procesos_simultaneos_no_duplican_carrera(self):
        contexto = multiprocessing.get_context("spawn")
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "historial.csv"
            carrera = Carrera()
            carrera.finalizar()
            comienzo = contexto.Event()
            resultado = contexto.Queue()
            procesos = [contexto.Process(target=guardar_en_proceso,
                        args=(ruta, carrera.resumen, comienzo, resultado)) for _ in range(6)]
            try:
                for proceso in procesos:
                    proceso.start()
                comienzo.set()
                for proceso in procesos:
                    proceso.join(15)
                    self.assertFalse(proceso.is_alive(), "Un proceso no terminó")
                    self.assertEqual(proceso.exitcode, 0)
                mensajes = [resultado.get(timeout=3) for _ in procesos]
                self.assertEqual(mensajes, ["ok"] * len(procesos))
                registros = HistorialCSV(ruta).consultar()
                self.assertEqual(len(registros), 1)
                self.assertEqual(registros[0]["identificador"], carrera.resumen.identificador)
            finally:
                for proceso in procesos:
                    if proceso.is_alive():
                        proceso.terminate()
                        proceso.join()
                resultado.close()
                resultado.join_thread()

    def test_historial_ocupado_retiene_carrera_hasta_reintento(self):
        contexto = multiprocessing.get_context("spawn")
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "historial.csv"
            preparado, terminar = contexto.Event(), contexto.Event()
            proceso = contexto.Process(target=mantener_bloqueo, args=(ruta, preparado, terminar))
            proceso.start()
            try:
                self.assertTrue(preparado.wait(10), "El proceso no obtuvo el bloqueo")
                gestor = GestorTerminal(historial=HistorialCSV(ruta))
                gestor.ejecutar("iniciar")
                mensaje, _ = gestor.ejecutar("finalizar")
                self.assertIn("otra ejecución", mensaje)
                self.assertIsNotNone(gestor.pendiente)
                self.assertFalse(gestor.ejecutar("salir")[1])
            finally:
                terminar.set()
                proceso.join(10)
                if proceso.is_alive():
                    proceso.terminate()
                    proceso.join()
            self.assertEqual(proceso.exitcode, 0)
            self.assertIn("guardada", gestor.ejecutar("reintentar")[0])
            self.assertEqual(len(HistorialCSV(ruta).consultar()), 1)
