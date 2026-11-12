"""Preparación de una instalación nueva con credenciales ficticias."""
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from cryptography import x509
from ipaddress import ip_address

from taxiroute.bloqueo import bloqueo_historial
from taxiroute.instalacion_inicial import ARCHIVO_MARCADOR, preparar_instalacion, revisar_instalacion
from taxiroute.inicializacion_administrador import InicializacionCancelada
from taxiroute.verificar_historial_sqlite import verificar_historial_sqlite_vinculado


class PruebasInstalacionInicial(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(prefix="taxi iván ")
        self.addCleanup(temporal.cleanup)
        self.base = Path(temporal.name).resolve()
        self.carpeta = self.base / "instalacion nueva"
        self.datos = dict(usuario="AdminPrueba", nombre="Administración prueba", correo="demo@example.invalid",
                          contrasena="SeguraPrueba123!", confirmar_codigos=lambda codigos: len(codigos) == 5)

    def test_crea_administrador_propio_historial_vinculado_y_tls(self):
        rutas = preparar_instalacion(self.carpeta, **self.datos)
        self.assertEqual(revisar_instalacion(self.carpeta), rutas)
        with closing(sqlite3.connect(rutas.datos / "cuentas.sqlite3")) as conexion:
            usuario = conexion.execute("SELECT nombre_usuario, hash_contrasena, cambio_obligatorio FROM usuarios").fetchone()
            self.assertEqual((usuario[0], usuario[2]), ("AdminPrueba", 1))
            self.assertNotIn(self.datos["contrasena"], usuario[1])
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 5)
            self.assertEqual(conexion.execute("SELECT correo_verificacion FROM configuracion_administrador").fetchone()[0], "demo@example.invalid")
        with bloqueo_historial(rutas.datos / "servidor", espera=0):
            verificar_historial_sqlite_vinculado(rutas.datos / "cuentas.sqlite3", rutas.datos / "historial.sqlite3")
        cert=x509.load_pem_x509_certificate(rutas.certificado.read_bytes())
        san=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(ip_address("127.0.0.1"), san.get_values_for_type(x509.IPAddress))

    def test_primera_autenticacion_exige_codigo_y_cambio_de_clave(self):
        from taxiroute.servidor_sqlite_local import servidor_sqlite_experimental
        rutas=preparar_instalacion(self.carpeta, **self.datos)
        buzon=[]
        opciones=dict(ruta_cuentas=rutas.datos/"cuentas.sqlite3", ruta_historial=rutas.datos/"historial.sqlite3",
            ruta_tarifas=rutas.datos/"tarifas.json", certificado=rutas.certificado, clave=rutas.clave,
            puerto=0, enviar_pin=lambda destino,codigo: buzon.append((destino,codigo)))
        with servidor_sqlite_experimental(**opciones) as servidor:
            c=servidor.app.test_client(); url="https://localhost"; cabeceras={"Origin":url}
            def post(ruta,datos): return c.post(ruta,base_url=url,headers=cabeceras,json=datos)
            inicial=post("/acceso/administrador",{"usuario":"AdminPrueba","contrasena":self.datos["contrasena"]})
            self.assertEqual(inicial.json["paso"],"pin_pendiente")
            self.assertEqual(buzon[0][0],"demo@example.invalid")
            self.assertEqual(c.get("/administracion",base_url=url).status_code,401)
            pin=post("/acceso/administrador/pin",{"codigo":buzon[0][1]})
            self.assertEqual(pin.json["paso"],"cambio_pendiente")
            self.assertEqual(c.get("/administracion",base_url=url).status_code,401)
            cambio=post("/acceso/administrador/cambio",{"contrasena_nueva":"NuevaClave456!"})
            self.assertEqual(cambio.json["paso"],"volver_a_iniciar")
            self.assertEqual(c.get("/administracion",base_url=url).status_code,401)

    def test_cambio_inicial_y_segundo_acceso_con_correo_simulado(self):
        from datetime import datetime, timedelta, timezone
        from secrets import token_bytes
        from taxiroute.composicion_experimental import abrir_app_experimental
        rutas=preparar_instalacion(self.carpeta, **self.datos); buzon=[]
        ahora=[datetime(2026, 11, 12, tzinfo=timezone.utc)]
        with abrir_app_experimental(ruta_cuentas=rutas.datos/"cuentas.sqlite3",
                ruta_historial=rutas.datos/"historial.sqlite3", ruta_tarifas=rutas.datos/"tarifas.json",
                clave_hmac=token_bytes(32), enviar_pin=lambda destino,codigo: buzon.append((destino,codigo)),
                reloj=lambda: ahora[0]) as app:
            c=app.test_client(); url="https://localhost"; cabeceras={"Origin":url}
            def post(ruta,datos): return c.post(ruta,base_url=url,headers=cabeceras,json=datos)
            inicial=post("/acceso/administrador",{"usuario":"AdminPrueba","contrasena":self.datos["contrasena"]})
            self.assertEqual(inicial.json["paso"],"pin_pendiente")
            self.assertEqual(post("/acceso/administrador/pin",{"codigo":buzon[-1][1]}).json["paso"],"cambio_pendiente")
            self.assertEqual(post("/acceso/administrador/cambio",{"contrasena_nueva":"NuevaClave456!"}).json["paso"],"volver_a_iniciar")
            ahora[0]+=timedelta(minutes=2)
            self.assertEqual(post("/acceso/administrador",{"usuario":"AdminPrueba","contrasena":"NuevaClave456!"}).json["paso"],"pin_pendiente")
            self.assertEqual(post("/acceso/administrador/pin",{"codigo":buzon[-1][1]}).json["paso"],"acceso_completado")
            self.assertEqual(c.get("/administracion",base_url=url).status_code,200)
            self.assertEqual(post("/acceso/cerrar",{}).status_code,200)
            self.assertEqual(post("/acceso/administrador",{"usuario":"AdminPrueba","contrasena":self.datos["contrasena"]}).status_code,401)

    def test_no_sobrescribe_carpeta_existente(self):
        self.carpeta.mkdir(); original=self.carpeta / "importante.txt"; original.write_text("conservar",encoding="utf-8")
        with self.assertRaises(FileExistsError): preparar_instalacion(self.carpeta, **self.datos)
        self.assertEqual(original.read_text(encoding="utf-8"), "conservar")
        self.assertEqual(sorted(p.name for p in self.carpeta.iterdir()), ["importante.txt"])

    def test_cancelar_codigos_no_crea_marcador_ni_administrador(self):
        self.datos["confirmar_codigos"] = lambda codigos: False
        with self.assertRaises(InicializacionCancelada): preparar_instalacion(self.carpeta, **self.datos)
        self.assertFalse((self.carpeta / ARCHIVO_MARCADOR).exists())
        with closing(sqlite3.connect(self.carpeta / "datos" / "cuentas.sqlite3")) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
        with self.assertRaises(ValueError): revisar_instalacion(self.carpeta)

    def test_fallo_parcial_no_se_acepta_ni_se_elimina(self):
        with patch("taxiroute.instalacion_inicial._generar_certificado", side_effect=OSError("fallo ficticio")):
            with self.assertRaises(OSError): preparar_instalacion(self.carpeta, **self.datos)
        self.assertTrue((self.carpeta / "datos" / "cuentas.sqlite3").exists())
        self.assertFalse((self.carpeta / ARCHIVO_MARCADOR).exists())
        with self.assertRaises(ValueError): revisar_instalacion(self.carpeta)

    def test_rechaza_contrasena_invalida_y_repositorio_antes_de_crear(self):
        with self.assertRaises(ValueError): preparar_instalacion(self.carpeta, **(self.datos | {"contrasena":"corta"}))
        self.assertFalse(self.carpeta.exists())
        (self.base / ".git").mkdir()
        with self.assertRaises(ValueError): preparar_instalacion(self.carpeta, **self.datos)
        self.assertFalse(self.carpeta.exists())

    def test_rechaza_correos_sin_usuario_o_dominio_antes_de_crear(self):
        for correo in ("@example.com", "usuario@"):
            with self.subTest(correo=correo), self.assertRaises(ValueError):
                preparar_instalacion(self.carpeta, **(self.datos | {"correo": correo}))
            self.assertFalse(self.carpeta.exists())

    def test_rechaza_union_en_ruta_antes_de_crear_archivos(self):
        from taxiroute import instalacion_inicial
        original = instalacion_inicial._es_redireccion
        with patch("taxiroute.instalacion_inicial._es_redireccion",
                   side_effect=lambda p: Path(p) == self.base or original(p)):
            with self.assertRaises(ValueError): preparar_instalacion(self.carpeta, **self.datos)
        self.assertFalse(self.carpeta.exists())

    def test_rechaza_marcador_modificado_sin_tocar_datos(self):
        rutas=preparar_instalacion(self.carpeta, **self.datos)
        (self.carpeta / ARCHIVO_MARCADOR).write_text("otra instalación",encoding="utf-8")
        with self.assertRaises(ValueError): revisar_instalacion(self.carpeta)
        self.assertTrue((rutas.datos / "historial.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
