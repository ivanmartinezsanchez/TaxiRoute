"""Primera cuenta: solo datos y bases temporales, sin Gmail ni red."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from secrets import token_bytes
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import DatosCuentaInvalidos, _PROTECTOR, crear_cuenta
from taxiroute.inicializacion_administrador import (InicializacionCancelada, InicializacionDenegada, preparar_primer_administrador,
    preparar_primer_administrador_con_codigos)
from taxiroute.codigos_recuperacion_experimentales import consumir_codigo_en_transaccion
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

    def test_crea_admin_y_codigos_conjuntamente_sin_mostrarlos_en_repr(self):
        resultado = preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        self.assertEqual(len(set(resultado.codigos)), 5)
        self.assertTrue(resultado.cuenta.cambio_obligatorio)
        for codigo in resultado.codigos:
            self.assertNotIn(codigo, repr(resultado))
        with conectar(self.ruta) as conexion:
            filas = conexion.execute("SELECT usuario_id, verificador FROM codigos_recuperacion").fetchall()
            self.assertEqual(len(filas), 5)
            self.assertTrue(all(fila[0] == resultado.cuenta.id and fila[1] not in resultado.codigos for fila in filas))
            conexion.execute("BEGIN IMMEDIATE")
            self.assertTrue(consumir_codigo_en_transaccion(conexion, resultado.cuenta.id, resultado.codigos[0]))
            conexion.rollback()

    def test_confirmacion_de_codigos_ocurre_antes_de_crear_la_cuenta(self):
        vistos = []
        def confirmar(codigos):
            vistos.extend(codigos)
            with conectar(self.ruta) as conexion:
                self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 0)
            return True
        resultado = preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos,
                                                             confirmar_codigos=confirmar)
        self.assertEqual(tuple(vistos), resultado.codigos)
        with conectar(self.ruta) as conexion:
            conexion.execute("BEGIN IMMEDIATE")
            self.assertTrue(consumir_codigo_en_transaccion(conexion, resultado.cuenta.id, vistos[0]))
            conexion.rollback()

    def test_cancelar_confirmacion_no_crea_ningun_dato(self):
        with self.assertRaises(InicializacionCancelada):
            preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos,
                                                     confirmar_codigos=lambda codigos: False)
        with conectar(self.ruta) as conexion:
            for tabla in ("usuarios", "configuracion_administrador", "codigos_recuperacion"):
                self.assertEqual(conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0], 0)

    def test_cuenta_creada_durante_confirmacion_no_se_sobrescribe(self):
        def confirmar(codigos):
            crear_cuenta(self.ruta, nombre_usuario="Otro", nombre_visible="Prueba",
                         contrasena="ConductorPrueba123!")
            return True
        with self.assertRaises(InicializacionDenegada):
            preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos, confirmar_codigos=confirmar)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT nombre_usuario FROM usuarios").fetchone()[0], "Otro")
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 0)

    def test_error_al_generar_codigos_revierte_toda_la_inicializacion(self):
        with patch("taxiroute.inicializacion_administrador.generar_codigos_en_transaccion",
                   side_effect=OSError("fallo simulado")):
            with self.assertRaises(OSError):
                preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        with conectar(self.ruta) as conexion:
            for tabla in ("usuarios", "configuracion_administrador", "codigos_recuperacion"):
                self.assertEqual(conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0], 0)
        resultado = preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        self.assertEqual(len(resultado.codigos), 5)

    def test_no_emite_segundo_lote_sobre_cuenta_existente(self):
        resultado = preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        with self.assertRaises(InicializacionDenegada):
            preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion WHERE usuario_id=?",
                                              (resultado.cuenta.id,)).fetchone()[0], 5)

    def test_dos_altas_con_codigos_solo_producen_un_lote(self):
        def intentar(numero):
            try:
                return preparar_primer_administrador_con_codigos(self.ruta,
                    **(self.argumentos | {"nombre_usuario": f"Admin{numero}"}))
            except InicializacionDenegada:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, (1, 2)))
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
        with conectar(self.ruta) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM codigos_recuperacion").fetchone()[0], 5)

    def test_fallo_a_mitad_del_lote_revierte_tambien_cuenta_y_correo(self):
        with conectar(self.ruta) as conexion:
            conexion.execute("""CREATE TRIGGER fallo_segundo_codigo BEFORE INSERT ON codigos_recuperacion
                WHEN (SELECT COUNT(*) FROM codigos_recuperacion) > 0
                BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            preparar_primer_administrador_con_codigos(self.ruta, **self.argumentos)
        with conectar(self.ruta) as conexion:
            for tabla in ("usuarios", "configuracion_administrador", "codigos_recuperacion"):
                self.assertEqual(conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0], 0)

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
