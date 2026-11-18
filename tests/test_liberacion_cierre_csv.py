"""Cierre CSV experimental con usuarios y archivos temporales."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
import unittest

from taxiroute.base_cuentas import conectar, inicializar_base
from taxiroute.carrera import Carrera
from taxiroute.conciliacion_csv import ConciliacionCSVError, conciliar_pendiente_csv
from taxiroute.cuentas import crear_cuenta
from taxiroute.flota import FlotaBloqueada, FlotaEnMemoria, OperacionNoPermitida
from taxiroute.historial import HistorialCSV
from taxiroute.intenciones_operacion import registrar_intencion
from taxiroute.liberacion_cierre_csv import liberar_cierre_csv
from taxiroute.servicio import ServicioCarreras
from taxiroute.sesiones import SesionNoAutorizada, ServicioSesiones


class PruebasLiberacionCSV(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.base = carpeta / "cuentas.sqlite3"
        self.historiales = carpeta / "historiales"
        self.historiales.mkdir()
        inicializar_base(self.base)
        self.ana = crear_cuenta(self.base, nombre_usuario="Ana", nombre_visible="Ana", contrasena="Conductor123!")
        self.luis = crear_cuenta(self.base, nombre_usuario="Luis", nombre_visible="Luis", contrasena="Conductor123!")
        with conectar(self.base) as conexion:
            conexion.execute("UPDATE usuarios SET cambio_obligatorio=0")
        self.fecha = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.tiempo = 0
        self.sesiones = ServicioSesiones(self.base, reloj=lambda: self.fecha)
        self.token = self.sesiones.iniciar_conductor("Ana", "Conductor123!").token
        self.otro_token = self.sesiones.iniciar_conductor("Luis", "Conductor123!").token
        self.flota = FlotaEnMemoria(lambda _: ServicioCarreras(crear_carrera=lambda: Carrera(
            reloj=lambda: self.tiempo, fecha_actual=lambda: self.fecha)), modo_durable_experimental=True)
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador, accion="iniciar", revision=0)
        self.flota.aplicar_intencion_experimental(self.sesiones, self.token, identificador)
        self.fecha += timedelta(seconds=10)
        self.tiempo += 10_000_000_000
        self.pendiente = self.flota.preparar_cierre_durable_experimental(self.sesiones, self.token, 1)
        self.csv = self.historiales / self.ana.id / "historial.csv"

    def conciliar(self):
        return conciliar_pendiente_csv(self.base, self.csv, self.ana.id, self.pendiente.punto.carrera_id)

    def liberar(self, token=None):
        return liberar_cierre_csv(self.flota, self.sesiones, token or self.token, self.historiales)

    def test_cierre_csv_verificado_y_siguiente_carrera(self):
        self.conciliar()
        resultado = self.liberar()
        self.assertEqual(str(resultado.importe), "0.20")
        self.assertFalse(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)
        identificador = str(uuid4())
        registrar_intencion(self.sesiones, self.token, id_intencion=identificador, accion="iniciar", revision=2)
        nueva = self.flota.aplicar_intencion_experimental(self.sesiones, self.token, identificador)
        self.assertEqual(nueva.revision, 3)
        self.assertNotEqual(nueva.carrera_id, resultado.identificador)

    def test_sin_csv_no_libera_ni_crea_historial(self):
        with self.assertRaises(OperacionNoPermitida):
            self.liberar()
        self.assertFalse(self.csv.exists())
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_csv_modificado_no_libera_carrera(self):
        self.conciliar()
        texto = self.csv.read_text(encoding="utf-8")
        self.csv.write_text(texto.replace(",0.20\n", ",999.00\n"), encoding="utf-8", newline="")
        with self.assertRaises(ConciliacionCSVError):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_otro_conductor_o_sesion_revocada_no_liberan(self):
        self.conciliar()
        with self.assertRaises((OperacionNoPermitida, FlotaBloqueada)):
            self.liberar(self.otro_token)
        self.sesiones.cerrar(self.token)
        with self.assertRaises(SesionNoAutorizada):
            self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))

    def test_sesion_revocada_entre_seleccion_y_validacion_no_libera(self):
        from taxiroute.liberacion_cierre_csv import _historial_de_cuenta
        self.conciliar()
        def seleccionar_y_revocar(carpeta, usuario):
            historial = _historial_de_cuenta(carpeta, usuario)
            self.sesiones.cerrar(self.token)
            return historial
        with patch("taxiroute.liberacion_cierre_csv._historial_de_cuenta", side_effect=seleccionar_y_revocar):
            with self.assertRaises(SesionNoAutorizada):
                self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_dos_liberaciones_simultaneas_solo_una_tiene_exito(self):
        self.conciliar()
        barrera = Barrier(2)
        def intentar(_):
            barrera.wait(timeout=5)
            try:
                return self.liberar()
            except FlotaBloqueada:
                return None
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(intentar, range(2)))
        self.assertEqual(sum(resultado is not None for resultado in resultados), 1)
        self.assertEqual(len(HistorialCSV(self.csv).consultar()), 1)

    def test_error_al_cerrar_transaccion_restaura_memoria_y_permita_reintentar(self):
        from taxiroute.liberacion_cierre_csv import _conectar_existente
        self.conciliar()
        def conexion_con_fallo(ruta):
            class Proxy:
                def __init__(self, real):
                    self.real, self.fallo = real, False
                def __getattr__(self, nombre):
                    return getattr(self.real, nombre)
                def rollback(self):
                    self.real.rollback()
                    if not self.fallo:
                        self.fallo = True
                        raise OSError("fallo simulado")
            return Proxy(_conectar_existente(ruta))
        with patch("taxiroute.liberacion_cierre_csv._conectar_existente", side_effect=conexion_con_fallo):
            with self.assertRaises(OSError):
                self.liberar()
        self.assertTrue(self.flota.requiere_reconciliacion(self.ana.id))
        self.assertEqual(self.flota._cierres_preparados[self.ana.id], self.pendiente)
        self.assertEqual(self.liberar().identificador, self.pendiente.punto.carrera_id)


if __name__ == "__main__":
    unittest.main()
