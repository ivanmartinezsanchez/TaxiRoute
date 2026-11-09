"""Primera cuenta: solo datos y bases temporales, sin Gmail ni red."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import DatosCuentaInvalidos, _PROTECTOR, crear_cuenta
from taxiroute.inicializacion_administrador import InicializacionDenegada, preparar_primer_administrador
from taxiroute.cambio_administrador import cambiar_contrasena_administrador
from taxiroute.pin import ServicioPIN
from taxiroute.acceso import comprobar_credenciales, PasoAcceso


class PruebasPrimerAdministrador(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.argumentos = dict(nombre_usuario="AdminPrueba", nombre_visible="Admin de prueba",
                               contrasena="TemporalPrueba123!", correo="prueba@example.invalid")

    def preparar(self, **cambios):
        return preparar_primer_administrador(self.ruta, **(self.argumentos | cambios))

    def test_crea_cuenta_y_correo_juntos_con_hash_y_cambio_obligatorio(self):
        cuenta = self.preparar()
        self.assertTrue(cuenta.cambio_obligatorio)
        with conectar(self.ruta) as conexion:
            usuario = conexion.execute("SELECT * FROM usuarios WHERE id=?", (cuenta.id,)).fetchone()
            configuracion = conexion.execute("SELECT * FROM configuracion_administrador").fetchone()
        self.assertEqual(usuario["rol"], "administrador")
        self.assertEqual(usuario["cambio_obligatorio"], 1)
        self.assertNotIn(self.argumentos["contrasena"], usuario["hash_contrasena"])
        self.assertTrue(_PROTECTOR.verify(usuario["hash_contrasena"], self.argumentos["contrasena"]))
        self.assertEqual((configuracion["usuario_id"], configuracion["correo_verificacion"]),
                         (cuenta.id, self.argumentos["correo"]))

    def test_rechaza_repetir_sin_alterar_cuenta_existente(self):
        primera = self.preparar()
        with self.assertRaises(InicializacionDenegada):
            self.preparar(nombre_usuario="OtraCuenta")
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT id FROM usuarios").fetchall()[0][0], primera.id)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)

    def test_cuenta_nueva_verifica_pin_y_cambia_contrasena(self):
        self.preparar()
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32),
                          enviar=lambda destinatario, codigo: buzon.append((destinatario, codigo)))
        desafio = pin.solicitar("AdminPrueba", self.argumentos["contrasena"])
        self.assertIsNotNone(desafio)
        self.assertEqual(buzon[0][0], self.argumentos["correo"])
        resultado = pin.verificar(desafio, buzon[0][1])
        self.assertTrue(resultado.cambio_obligatorio)
        self.assertIsNone(resultado.sesion)
        nueva = "NuevaClave456!"
        cambiar_contrasena_administrador(self.ruta, resultado.autorizacion_cambio, nueva)
        acceso = comprobar_credenciales(self.ruta, "AdminPrueba", nueva)
        self.assertEqual(acceso.paso, PasoAcceso.PIN_PENDIENTE)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT cambio_obligatorio FROM usuarios").fetchone()[0], 0)

    def test_un_fallo_al_guardar_el_correo_revierte_la_cuenta(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_correo BEFORE INSERT ON configuracion_administrador
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.preparar()
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM configuracion_administrador").fetchone()[0], 0)

    def test_base_con_conductor_existente_no_se_inicializa(self):
        crear_cuenta(self.ruta, nombre_usuario="Conductor", nombre_visible="Prueba",
                     contrasena="ConductorPrueba123!")
        with self.assertRaises(InicializacionDenegada):
            self.preparar()
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT rol FROM usuarios").fetchone()[0], "conductor")
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM configuracion_administrador").fetchone()[0], 0)

    def test_base_ajena_no_se_modifica(self):
        ajena = self.ruta.parent / "ajena.sqlite3"
        with closing(sqlite3.connect(ajena)) as conexion, conexion:
            conexion.execute("CREATE TABLE original (valor TEXT)")
            conexion.execute("INSERT INTO original VALUES ('conservar')")
        with self.assertRaises(InicializacionDenegada):
            preparar_primer_administrador(ajena, **self.argumentos)
        with closing(sqlite3.connect(ajena)) as conexion:
            self.assertEqual(conexion.execute("SELECT valor FROM original").fetchone()[0], "conservar")

    def test_datos_invalidos_y_base_inexistente_no_crean_cuentas(self):
        for cambios in ({"contrasena": "breve"}, {"correo": "a@example.invalid, b@example.invalid"}):
            with self.subTest(cambios=cambios), self.assertRaises(DatosCuentaInvalidos):
                self.preparar(**cambios)
        ausente = self.ruta.parent / "ausente.sqlite3"
        with self.assertRaises(InicializacionDenegada):
            preparar_primer_administrador(ausente, **self.argumentos)
        self.assertFalse(ausente.exists())
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)

    def test_dos_inicializaciones_simultaneas_crean_una_sola_cuenta(self):
        def intentar(numero):
            try:
                self.preparar(nombre_usuario=f"Admin{numero}")
                return True
            except InicializacionDenegada:
                return False
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, (1, 2)))
        self.assertEqual(sum(resultados), 1)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM configuracion_administrador").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
