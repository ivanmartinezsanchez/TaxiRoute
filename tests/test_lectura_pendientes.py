"""Pruebas aisladas con cuentas y bases temporales ficticias."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.cuentas import crear_cuenta
from taxiroute.finalizacion_pendiente import preparar_punto_final
from taxiroute.lectura_pendientes import PendienteInvalida, consultar_pendiente_propia
from taxiroute.pin import ServicioPIN
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.sesiones import ServicioSesiones, SesionNoAutorizada


class PruebasLecturaPendientes(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.ruta = Path(temporal.name) / "cuentas.sqlite3"
        inicializar_base(self.ruta)
        self.clave = "FicticiaLarga123!"
        self.ana = crear_cuenta(self.ruta, nombre_usuario="Ana", nombre_visible="Ana", contrasena=self.clave)
        self.luis = crear_cuenta(self.ruta, nombre_usuario="Luis", nombre_visible="Luis", contrasena=self.clave)
        self.admin = crear_cuenta(self.ruta, nombre_usuario="Admin", nombre_visible="Admin",
                                  contrasena=self.clave, rol="administrador")
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
            conexion.execute("INSERT INTO configuracion_administrador VALUES (?, ?)",
                             (self.admin.id, "admin@example.invalid"))
        self.fecha = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.sesiones = ServicioSesiones(self.ruta, reloj=lambda: self.fecha)
        self.ana_token = self.sesiones.iniciar_conductor("Ana", self.clave).token
        self.luis_token = self.sesiones.iniciar_conductor("Luis", self.clave).token
        buzon = []
        pin = ServicioPIN(self.ruta, clave_hmac=token_bytes(32), reloj=lambda: self.fecha,
                          enviar=lambda correo, codigo: buzon.append(codigo))
        desafio = pin.solicitar("Admin", self.clave)
        self.admin_token = pin.verificar(desafio, buzon[-1]).sesion.token
        self.punto = PuntoControl("carrera-ana", self.ana.id, self.fecha,
                                  self.fecha + timedelta(seconds=5), "parado",
                                  5_000_000_000, 0, "0.02", "0.05", 1)

    def consultar(self, token):
        return consultar_pendiente_propia(self.sesiones, token)

    def test_aislamiento_y_administrador_ve_solo_su_carrera(self):
        guardar_punto(self.ruta, self.punto)
        self.assertEqual(self.consultar(self.ana_token).punto, self.punto)
        self.assertIsNone(self.consultar(self.luis_token))
        self.assertIsNone(self.consultar(self.admin_token))
        otro = replace(self.punto, carrera_id="carrera-admin", usuario_id=self.admin.id)
        guardar_punto(self.ruta, otro)
        self.assertEqual(self.consultar(self.admin_token).punto, otro)
        self.assertEqual(self.consultar(self.ana_token).punto, self.punto)

    def test_caducidad_y_revocacion_impiden_lectura(self):
        guardar_punto(self.ruta, self.punto)
        self.sesiones.cerrar(self.luis_token)
        with self.assertRaises(SesionNoAutorizada):
            self.consultar(self.luis_token)
        self.fecha += timedelta(hours=1)
        with self.assertRaises(SesionNoAutorizada):
            self.consultar(self.ana_token)

    def test_estado_pendiente_final_valida_importe_y_no_se_modifica(self):
        preparar_punto_final(self.ruta, self.punto, self.punto.ultimo_control)
        resultado = self.consultar(self.ana_token)
        self.assertEqual(resultado.estado, "guardado_pendiente")
        self.assertEqual(str(resultado.importe_final), "0.10")
        self.assertEqual(resultado.fin, self.punto.ultimo_control)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET importe_final='99.99'")
        with self.assertRaises(PendienteInvalida):
            self.consultar(self.ana_token)

    def test_fecha_corrupta_y_estado_desconocido_fallan_cerrado(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET ultimo_control='incorrecta'")
        with self.assertRaises(PendienteInvalida):
            self.consultar(self.ana_token)
        with conectar(self.ruta) as conexion:
            conexion.execute("PRAGMA ignore_check_constraints=ON")
            conexion.execute("UPDATE carreras_recuperacion SET ultimo_control=?, estado_registro='desconocido'",
                             (self.punto.ultimo_control.isoformat(),))
        with self.assertRaises(PendienteInvalida):
            self.consultar(self.ana_token)

    def test_varias_pendientes_corruptas_fallan_cerrado(self):
        guardar_punto(self.ruta, self.punto)
        with conectar(self.ruta) as conexion:
            conexion.execute("DROP INDEX una_carrera_pendiente_por_usuario")
        guardar_punto(self.ruta, replace(self.punto, carrera_id="otra"))
        with self.assertRaises(PendienteInvalida):
            self.consultar(self.ana_token)

    def test_guardada_no_aparece_y_lecturas_no_renuevan_sesion(self):
        preparar_punto_final(self.ruta, self.punto, self.punto.ultimo_control)
        with conectar(self.ruta) as conexion:
            conexion.execute("UPDATE carreras_recuperacion SET estado_registro='guardada'")
        self.fecha += timedelta(minutes=59)
        self.assertIsNone(self.consultar(self.ana_token))
        self.fecha += timedelta(minutes=1)
        with self.assertRaises(SesionNoAutorizada):
            self.consultar(self.ana_token)


if __name__ == "__main__":
    unittest.main()
