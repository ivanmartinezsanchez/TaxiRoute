"""Pruebas aisladas de la comprobación previa; no ejecutan Docker."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import sqlite3

from taxiroute.preparacion_actualizacion import PreparacionActualizacionError, instalacion_detenida
from taxiroute.bloqueo import HistorialOcupado
from taxiroute.instalacion_inicial import preparar_instalacion
from taxiroute.puntos_control import PuntoControl, guardar_punto
from taxiroute.reutilizacion_proxy_experimental import MARCADOR_ACTUALIZACION, _bloqueo_instalacion
from taxiroute.verificador_proxy_experimental import EstadoComposicion, EstadoContenedor


def estado(backend=False, proxy=False):
    return EstadoComposicion(EstadoContenedor("1" * 64, "sha256:" + "a" * 64, "inicio", 0, backend),
                             EstadoContenedor("2" * 64, "sha256:" + "b" * 64, "inicio", 0, proxy))


class PruebasPreparacion(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="taxi-actualiza-")
        self.addCleanup(temp.cleanup)
        self.raiz = Path(temp.name).resolve() / "instalacion"
        self.rutas = preparar_instalacion(self.raiz, usuario="AdminPrueba", nombre="Prueba",
            correo="prueba@example.invalid", contrasena="Contrasena123!", confirmar_codigos=lambda _: True)
        self.identidad = self.raiz / "proxy-local"
        self.identidad.mkdir()
        self.manifiesto = SimpleNamespace(proyecto="ficticio", imagenes={}, contenedores={},
            rutas={"datos": str(self.rutas.datos)})
        prefijo = "taxiroute.preparacion_actualizacion."
        self.acl = patch(prefijo + "verificar_permisos_windows").start()
        self.man = patch(prefijo + "leer_manifiesto", return_value=self.manifiesto).start()
        self.ver = patch(prefijo + "verificar_para_reutilizar", return_value=estado()).start()
        self.docker = patch(prefijo + "_docker", return_value=b"").start()
        self.addCleanup(patch.stopall)

    def test_detenida_valida_datos_y_mantiene_bloqueo(self):
        with instalacion_detenida(self.raiz) as manifiesto:
            self.assertIs(manifiesto, self.manifiesto)
            with self.assertRaises(HistorialOcupado):
                with _bloqueo_instalacion(self.identidad):
                    pass
        self.assertEqual(self.ver.call_count, 2)
        self.assertEqual(self.docker.call_count, 2)

    def test_servicio_activo_no_permite_continuar(self):
        self.ver.return_value = estado(backend=True)
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")

    def test_otro_contenedor_activo_no_permite_continuar(self):
        self.docker.return_value = b"id-ficticio\n"
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")

    def test_marcador_pendiente_no_consulta_docker(self):
        (self.identidad / MARCADOR_ACTUALIZACION).write_text("pendiente", encoding="utf-8")
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")
        self.docker.assert_not_called()

    def test_carrera_pendiente_impide_continuar(self):
        with closing(sqlite3.connect(self.rutas.datos / "cuentas.sqlite3")) as c:
            usuario = c.execute("SELECT id FROM usuarios").fetchone()[0]
        instante = datetime.now(timezone.utc)
        guardar_punto(self.rutas.datos / "cuentas.sqlite3", PuntoControl("carrera-ficticia", usuario, instante,
            instante + timedelta(seconds=3), "parado", 3_000_000_000, 0, "0.02", "0.05", 1))
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")

    def test_cambio_de_estado_durante_comprobacion_impide_continuar(self):
        self.ver.side_effect = [estado(), estado(proxy=True)]
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")

    def test_datos_de_otra_instalacion_se_rechazan_antes_de_docker(self):
        otra = self.raiz.parent / "otra"
        otra.mkdir()
        self.manifiesto.rutas["datos"] = str(otra)
        with self.assertRaises(PreparacionActualizacionError):
            with instalacion_detenida(self.raiz):
                self.fail("No debe permitir actualizar")
        self.docker.assert_not_called()

    def test_error_de_almacenamiento_no_expone_detalles(self):
        with patch("taxiroute.preparacion_actualizacion._validar", side_effect=OSError("ruta-privada-ficticia")):
            with self.assertRaises(PreparacionActualizacionError) as error:
                with instalacion_detenida(self.raiz):
                    self.fail("No debe permitir actualizar")
        self.assertNotIn("ruta-privada-ficticia", str(error.exception))

    def test_error_del_llamador_no_se_oculta_y_libera_el_bloqueo(self):
        with self.assertRaisesRegex(ValueError, "error ficticio"):
            with instalacion_detenida(self.raiz):
                raise ValueError("error ficticio")
        with _bloqueo_instalacion(self.identidad):
            pass
