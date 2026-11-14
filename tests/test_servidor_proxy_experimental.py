"""Configuración del servidor tras proxy, sin Docker ni credenciales reales."""
from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
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


    @patch("taxiroute.servidor_proxy_experimental.crear_servidor_gunicorn")
    def test_autoridad_privada_explicita_no_cambia_escucha_interna(self, crear):
        archivos = ["cuentas", "historial", "tarifas", "certificado", "clave", "secreto-gmail"]
        argumentos = [dato for nombre in archivos for dato in ("--" + nombre, nombre)]
        self.assertEqual(main(argumentos + ["--direccion", "192.168.12.34"]), 0)
        opciones = crear.call_args.args[0]
        self.assertEqual(opciones["escucha"], "127.0.0.1")
        self.assertEqual(opciones["aplicacion"]["direccion"], "192.168.12.34")
        self.assertEqual(opciones["aplicacion"]["puerto"], 18771)

    @patch("taxiroute.servidor_proxy_experimental.crear_servidor_gunicorn")
    def test_rechaza_direcciones_publicas_o_inespecificas_antes_de_arrancar(self, crear):
        archivos = ["cuentas", "historial", "tarifas", "certificado", "clave", "secreto-gmail"]
        argumentos = [dato for nombre in archivos for dato in ("--" + nombre, nombre)]
        for direccion in ("0.0.0.0", "8.8.8.8", "localhost", "::1"):
            with self.subTest(direccion=direccion), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(argumentos + ["--direccion", direccion])
        crear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
