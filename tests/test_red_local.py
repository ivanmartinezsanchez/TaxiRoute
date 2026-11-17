import unittest
from unittest.mock import patch

from taxiroute.red_local import DireccionRedNoDisponible, comprobar_direccion_local


class PruebasDireccionLocal(unittest.TestCase):
    @patch("taxiroute.red_local.socket.socket")
    def test_direccion_privada_comprueba_asignacion_y_cierra(self, crear):
        socket = crear.return_value.__enter__.return_value
        self.assertEqual(comprobar_direccion_local("192.168.1.25"), "192.168.1.25")
        socket.bind.assert_called_once_with(("192.168.1.25", 0))
        crear.return_value.__exit__.assert_called_once()

    @patch("taxiroute.red_local.socket.socket")
    def test_rechaza_direcciones_publicas_y_no_especificas_sin_abrir_socket(self, crear):
        for direccion in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "::1", "192.168.001.1", None):
            with self.subTest(direccion=direccion), self.assertRaises(DireccionRedNoDisponible):
                comprobar_direccion_local(direccion)
        crear.assert_not_called()

    @patch("taxiroute.red_local.socket.socket")
    def test_rechaza_direccion_no_asignada(self, crear):
        crear.return_value.__enter__.return_value.bind.side_effect = OSError("no asignada")
        with self.assertRaises(DireccionRedNoDisponible):
            comprobar_direccion_local("192.168.1.25")
        crear.return_value.__exit__.assert_called_once()

