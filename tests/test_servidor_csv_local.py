"""Servidor HTTPS local con credenciales, correo y archivos ficticios."""
from http.cookiejar import CookieJar
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.request import HTTPSHandler, HTTPCookieProcessor, Request, build_opener

from taxiroute.base_cuentas import conectar
from taxiroute.bloqueo import HistorialOcupado
from taxiroute.cuentas import crear_cuenta
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.inicializacion_administrador import preparar_primer_administrador_con_codigos
from taxiroute.preparar_csv_local import preparar_archivos_csv
from taxiroute.servidor_csv_local import main, servidor_csv_experimental


class PruebasServidorCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        raiz = Path(temporal.name)
        self.archivos = preparar_archivos_csv(raiz / "datos")
        self.certificado, self.clave_tls = certificado_temporal(raiz)
        admin = preparar_primer_administrador_con_codigos(self.archivos.cuentas, nombre_usuario="Admin",
            nombre_visible="Admin de prueba", contrasena="AdminPrueba123!", correo="prueba@example.invalid")
        conductor = crear_cuenta(self.archivos.cuentas, nombre_usuario="Ana", nombre_visible="Ana de prueba",
                                  contrasena="ConductorPrueba123!")
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0 WHERE id IN (?, ?)",
                             (admin.cuenta.id, conductor.id))
        self.buzon = []
        self.opciones = dict(carpeta=self.archivos.carpeta, certificado=self.certificado,
            clave=self.clave_tls, puerto=0, enviar_pin=lambda correo, codigo: self.buzon.append((correo, codigo)))

    def test_https_local_cuentas_carrera_y_csv_tras_reabrir(self):
        def recorrer(servidor, primera):
            self.assertEqual(servidor.server_address[0], "127.0.0.1")
            hilo = Thread(target=servidor.serve_forever, daemon=True)
            hilo.start()
            try:
                base = f"https://127.0.0.1:{servidor.server_port}"
                contexto = ssl.create_default_context(cafile=str(self.certificado))
                conductor = build_opener(HTTPSHandler(context=contexto), HTTPCookieProcessor(CookieJar()))
                admin = build_opener(HTTPSHandler(context=contexto), HTTPCookieProcessor(CookieJar()))
                def peticion(cliente, ruta, datos=None):
                    contenido = None if datos is None else json.dumps(datos).encode("utf-8")
                    cabeceras = {} if datos is None else {"Origin": base, "Content-Type": "application/json"}
                    with cliente.open(Request(base + ruta, data=contenido, headers=cabeceras), timeout=5) as respuesta:
                        return respuesta.status, json.load(respuesta)
                self.assertEqual(peticion(conductor, "/acceso/conductor",
                    {"usuario": "Ana", "contrasena": "ConductorPrueba123!"})[0], 200)
                if primera:
                    inicio = peticion(admin, "/acceso/administrador", {"usuario": "Admin", "contrasena": "AdminPrueba123!"})
                    self.assertEqual(inicio[1]["paso"], "pin_pendiente")
                    self.assertEqual(self.buzon[-1][0], "prueba@example.invalid")
                    self.assertEqual(peticion(admin, "/acceso/administrador/pin", {"codigo": self.buzon[-1][1]})[1]["paso"], "acceso_completado")
                    self.assertEqual(peticion(conductor, "/api/carrera/iniciar", {"revision": 0})[0], 200)
                    self.assertEqual(peticion(conductor, "/api/carrera/finalizar", {"revision": 1})[0], 200)
                self.assertEqual(len(peticion(conductor, "/api/historial")[1]["carreras"]), 1)
            finally:
                servidor.shutdown()
                hilo.join(timeout=5)
                self.assertFalse(hilo.is_alive())
        with servidor_csv_experimental(**self.opciones) as servidor:
            recorrer(servidor, True)
        with servidor_csv_experimental(**self.opciones) as servidor:
            recorrer(servidor, False)

    def test_no_acepta_segunda_instancia(self):
        with servidor_csv_experimental(**self.opciones):
            with self.assertRaises(HistorialOcupado):
                with servidor_csv_experimental(**self.opciones):
                    pass

    def test_no_arranca_sin_marcador_o_administrador(self):
        marcador = self.archivos.carpeta / "PREPARACION_EXPERIMENTAL.txt"
        marcador.unlink()
        with patch("taxiroute.servidor_csv_local.make_server") as crear:
            with self.assertRaises(ValueError):
                with servidor_csv_experimental(**self.opciones):
                    pass
        crear.assert_not_called()
        marcador.write_text("TaxiRoute: preparación experimental CSV, versión 1.\n", encoding="utf-8")
        with conectar(self.archivos.cuentas) as conexion:
            conexion.execute("UPDATE usuarios SET habilitado=0 WHERE rol='administrador'")
        with patch("taxiroute.servidor_csv_local.make_server") as crear:
            with self.assertRaises(ValueError):
                with servidor_csv_experimental(**self.opciones):
                    pass
        crear.assert_not_called()

    def test_certificado_invalido_no_inicia_servidor(self):
        with patch("taxiroute.servidor_csv_local.make_server") as crear:
            with self.assertRaises((ValueError, OSError)):
                with servidor_csv_experimental(**(self.opciones | {"certificado": self.clave_tls})):
                    pass
        crear.assert_not_called()

    def test_sin_autorizacion_gmail_no_acepta_solicitudes_ni_expone_error(self):
        servidor = Mock()
        @contextmanager
        def simulado(**argumentos):
            yield servidor
        errores = StringIO()
        with patch("taxiroute.servidor_csv_local.servidor_csv_experimental", simulado), \
                patch("taxiroute.oauth_gmail.cliente_gmail_autorizado", side_effect=RuntimeError("detalle privado")), \
                redirect_stderr(errores):
            resultado = main(["--carpeta", str(self.archivos.carpeta), "--certificado", str(self.certificado),
                              "--clave", str(self.clave_tls)])
        self.assertEqual(resultado, 1)
        servidor.serve_forever.assert_not_called()
        self.assertIn("Revisa la autorización", errores.getvalue())
        self.assertNotIn("detalle privado", errores.getvalue())

    def test_arranque_verifica_gmail_sin_enviar_mensaje(self):
        servidor = Mock()
        servidor.serve_forever.side_effect = KeyboardInterrupt
        @contextmanager
        def simulado(**argumentos):
            yield servidor
        with patch("taxiroute.servidor_csv_local.servidor_csv_experimental", simulado), \
                patch("taxiroute.oauth_gmail.cliente_gmail_autorizado") as gmail, \
                redirect_stdout(StringIO()):
            resultado = main(["--carpeta", str(self.archivos.carpeta), "--certificado", str(self.certificado),
                              "--clave", str(self.clave_tls)])
        self.assertEqual(resultado, 0)
        gmail.assert_called_once_with()
        gmail.return_value.users.assert_not_called()


if __name__ == "__main__":
    unittest.main()
