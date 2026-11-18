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
from uuid import uuid4

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

    def test_https_durable_recupera_la_carrera_tras_reiniciar_servidor(self):
        def recorrer(servidor, primera):
            hilo = Thread(target=servidor.serve_forever, daemon=True)
            hilo.start()
            try:
                base = f"https://127.0.0.1:{servidor.server_port}"
                contexto = ssl.create_default_context(cafile=str(self.certificado))
                cliente = build_opener(HTTPSHandler(context=contexto), HTTPCookieProcessor(CookieJar()))
                def peticion(ruta, datos=None):
                    contenido = None if datos is None else json.dumps(datos).encode("utf-8")
                    cabeceras = {} if datos is None else {"Origin": base, "Content-Type": "application/json"}
                    with cliente.open(Request(base + ruta, data=contenido, headers=cabeceras), timeout=5) as respuesta:
                        return json.load(respuesta)
                peticion("/acceso/conductor", {"usuario": "Ana", "contrasena": "ConductorPrueba123!"})
                if primera:
                    peticion("/api/carrera/iniciar", {"revision": 0, "id_operacion": str(uuid4())})
                    return
                punto = peticion("/api/carrera/revision-recuperacion")["pendiente"]
                respuesta = peticion("/api/carrera/confirmar-recuperacion", {
                    "revision": punto["revision_recuperacion"], "ultimo_control": punto["ultimo_control"],
                    "huella": punto["huella"], "confirmar": True})
                filas = peticion("/api/historial")["carreras"]
                self.assertEqual(len(filas), 1)
                self.assertEqual((filas[0]["fin"], filas[0]["importe"]),
                                 (punto["ultimo_control"], punto["importe_recuperado"]))
                self.assertFalse(respuesta["activa"])
            finally:
                servidor.shutdown()
                hilo.join(timeout=5)
                self.assertFalse(hilo.is_alive())
        with servidor_csv_experimental(**self.opciones, durable=True) as servidor:
            recorrer(servidor, True)
        with servidor_csv_experimental(**self.opciones, durable=True) as servidor:
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

    def test_arranque_no_depende_de_gmail_ni_accede_a_credenciales(self):
        servidor = Mock()
        servidor.serve_forever.side_effect = KeyboardInterrupt
        @contextmanager
        def simulado(**argumentos):
            yield servidor
        errores, salida = StringIO(), StringIO()
        with patch("taxiroute.servidor_csv_local.servidor_csv_experimental", simulado), \
                patch("taxiroute.oauth_gmail.cliente_gmail_autorizado", side_effect=RuntimeError("detalle privado")) as gmail, \
                redirect_stderr(errores), redirect_stdout(salida):
            resultado = main(["--carpeta", str(self.archivos.carpeta), "--certificado", str(self.certificado),
                              "--clave", str(self.clave_tls)])
        self.assertEqual(resultado, 0)
        servidor.serve_forever.assert_called_once()
        gmail.assert_not_called()
        self.assertIn("correo autorizado y conexión", salida.getvalue())
        self.assertNotIn("detalle privado", errores.getvalue() + salida.getvalue())

    def test_fallo_correo_permite_conductor_sin_saltar_pin_administrador(self):
        def falla_envio(_destinatario, _codigo):
            raise OSError("detalle privado simulado")
        with servidor_csv_experimental(**(self.opciones | {"enviar_pin": falla_envio})) as servidor:
            conductor = servidor.app.test_client()
            admin = servidor.app.test_client()
            def post(cliente, ruta, datos):
                return cliente.post(ruta, base_url="https://localhost", headers={"Origin": "https://localhost"}, json=datos)
            acceso = post(conductor, "/acceso/conductor", {"usuario": "Ana", "contrasena": "ConductorPrueba123!"})
            self.assertEqual(acceso.status_code, 200)
            inicio = post(conductor, "/api/carrera/iniciar", {"revision": 0})
            self.assertEqual(inicio.status_code, 200)
            fallo = post(admin, "/acceso/administrador", {"usuario": "Admin", "contrasena": "AdminPrueba123!"})
            self.assertEqual(fallo.status_code, 401)
            self.assertIn("disponibilidad del correo", fallo.json["error"])
            credenciales = post(admin, "/acceso/administrador", {"usuario": "Inexistente", "contrasena": "ConductorPrueba123!"})
            self.assertEqual((credenciales.status_code, credenciales.json["error"]), (401, fallo.json["error"]))
            self.assertNotIn("detalle privado", fallo.get_data(as_text=True))
            self.assertIsNone(admin.get_cookie("taxiroute_sesion"))
            self.assertIsNone(admin.get_cookie("taxiroute_pin_pendiente", path="/acceso/administrador"))
            self.assertNotEqual(admin.get("/administracion", base_url="https://localhost").status_code, 200)
            with conectar(self.archivos.cuentas) as conexion:
                desafio = conexion.execute("SELECT estado_envio, invalidado_en FROM desafios_acceso").fetchone()
                self.assertEqual(desafio["estado_envio"], "fallido")
                self.assertIsNotNone(desafio["invalidado_en"])

    def test_proveedor_solo_se_consulta_al_solicitar_envio(self):
        servidor = Mock()
        servidor.serve_forever.side_effect = KeyboardInterrupt
        capturado = {}
        @contextmanager
        def simulado(**argumentos):
            capturado.update(argumentos)
            yield servidor
        with patch("taxiroute.servidor_csv_local.servidor_csv_experimental", simulado), \
                patch("taxiroute.oauth_gmail.cliente_gmail_autorizado") as gmail, \
                patch("taxiroute.servidor_csv_local.EnviadorGmail") as transporte, \
                redirect_stdout(StringIO()):
            resultado = main(["--carpeta", str(self.archivos.carpeta), "--certificado", str(self.certificado),
                              "--clave", str(self.clave_tls)])
            self.assertEqual(resultado, 0)
            gmail.assert_not_called()
            transporte.assert_not_called()
            capturado["enviar_pin"]("prueba@example.invalid", "1234567")
            gmail.assert_called_once_with()
            transporte.assert_called_once_with(gmail.return_value)
            transporte.return_value.assert_called_once_with("prueba@example.invalid", "1234567")


if __name__ == "__main__":
    unittest.main()
