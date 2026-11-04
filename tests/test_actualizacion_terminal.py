"""Prueba de la barra de estado mientras se escribe un comando."""

from io import StringIO
from threading import Thread
from time import monotonic, sleep
import unittest

from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from taxiroute.carrera import Carrera
from taxiroute.terminal import GestorTerminal


class SalidaTerminal(StringIO):
    def isatty(self):
        return True


class PruebaActualizacion(unittest.TestCase):
    def test_barra_se_actualiza_sin_borrar_texto_en_curso(self):
        instante = [0]
        gestor = GestorTerminal(lambda: Carrera(lambda: instante[0]))
        gestor.ejecutar("iniciar")
        salida = SalidaTerminal()
        pantalla = Vt100_Output(salida, lambda: Size(rows=24, columns=100), term="xterm")
        resultado = []
        importes_mostrados = []

        def barra_estado():
            texto = gestor.estado_actual()
            importes_mostrados.append(texto)
            return texto
        with create_pipe_input() as entrada:
            sesion = PromptSession(
                bottom_toolbar=barra_estado, refresh_interval=0.1,
                input=entrada, output=pantalla,
            )
            hilo = Thread(target=lambda: resultado.append(sesion.prompt("TaxiRoute > ")))
            hilo.start()
            try:
                entrada.send_text("\x1b[1;1R")  # Respuesta del terminal a la consulta del cursor.
                entrada.send_text("mover")
                limite = monotonic() + 3
                while sesion.app.current_buffer.text != "mover" and monotonic() < limite:
                    sleep(0.02)
                self.assertEqual(sesion.app.current_buffer.text, "mover")
                instante[0] = 2_000_000_000
                limite = monotonic() + 3
                while not any("0.04" in texto for texto in importes_mostrados) and monotonic() < limite:
                    sleep(0.05)
                self.assertTrue(any("0.04" in texto for texto in importes_mostrados))
                self.assertEqual(sesion.app.current_buffer.text, "mover")
            finally:
                entrada.send_text("\n")
                hilo.join(timeout=3)
            self.assertFalse(hilo.is_alive())
            self.assertEqual(resultado, ["mover"])


if __name__ == "__main__":
    unittest.main()
