"""Configuración del servidor tras proxy, sin Docker ni credenciales reales."""
from pathlib import Path
import unittest
from unittest.mock import patch

from taxiroute.servidor_proxy_experimental import main


class PruebaServidorProxy(unittest.TestCase):
    @patch("taxiroute.servidor_proxy_experimental.crear_servidor_gunicorn")
    def test_separa_puerto_publico_e_interno_y_escucha_en_bucle(self, crear):
        archivos = ["cuentas", "historial", "tarifas", "certificado", "clave", "secreto-gmail"]
        argumentos = [dato for nombre in archivos for dato in ("--" + nombre, nombre)]
        self.assertEqual(main(argumentos), 0)
        opciones = crear.call_args.args[0]
        self.assertEqual(opciones["escucha"], "127.0.0.1")
        self.assertEqual(opciones["puerto"], 18772)
        self.assertEqual(opciones["aplicacion"]["direccion"], "127.0.0.1")
        self.assertEqual(opciones["aplicacion"]["puerto"], 18771)
        self.assertEqual(opciones["aplicacion"]["ruta_cuentas"], Path("cuentas"))
        crear.return_value.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
