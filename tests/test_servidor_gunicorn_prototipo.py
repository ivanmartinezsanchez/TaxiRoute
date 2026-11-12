"""Pruebas aisladas del prototipo; no requieren Gunicorn ni correo real."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import Mock, patch
import sys
import unittest

from taxiroute.bloqueo import HistorialOcupado
from taxiroute.demo_contenedor import certificado_temporal
from taxiroute.demo_sqlite_contenedor import preparar_demo_sqlite
from taxiroute.servidor_gunicorn_experimental import abrir_app_gunicorn, crear_servidor_gunicorn


class PruebasAplicacionPrototipo(unittest.TestCase):
    def setUp(self):
        temporal = TemporaryDirectory(); self.addCleanup(temporal.cleanup)
        raiz = Path(temporal.name)
        datos = preparar_demo_sqlite(raiz / "datos")
        certificado, clave = certificado_temporal(raiz)
        self.opciones = dict(ruta_cuentas=datos / "cuentas.sqlite3", ruta_historial=datos / "historial.sqlite3",
            ruta_tarifas=datos / "tarifas.json", certificado=certificado, clave=clave,
            puerto=18772, enviar_pin=lambda *_: None)

    def test_retiene_bloqueo_y_limita_host_origen(self):
        with abrir_app_gunicorn(**self.opciones) as app:
            self.assertTrue(app.config["RECUPERACION_HABILITADA"])
            with self.assertRaises(HistorialOcupado):
                with abrir_app_gunicorn(**self.opciones): pass
            cliente=app.test_client(); base="https://127.0.0.1:18772"
            self.assertEqual(cliente.get("/api/historial",base_url=base).status_code,401)
            self.assertEqual(cliente.get("/api/historial",base_url="https://127.0.0.1:18773").status_code,400)
            self.assertEqual(cliente.get("/api/historial",base_url="https://localhost:18772").status_code,400)
            cuerpo={"usuario":"AnaDemo", "contrasena":"ConductorDemo123!"}
            self.assertEqual(cliente.post("/acceso/conductor",base_url=base,
                headers={"Origin":"https://ajeno.invalid"},json=cuerpo).status_code,403)
            self.assertEqual(cliente.post("/acceso/conductor",base_url=base,
                headers={"Origin":base},json=cuerpo).status_code,200)
            self.assertEqual(cliente.get("/api/historial",base_url=base).status_code,200)
        with abrir_app_gunicorn(**self.opciones): pass

    def test_rechaza_san_ajeno_y_libera_bloqueo_tras_fallo(self):
        from ipaddress import ip_address
        with self.assertRaises(ValueError):
            with abrir_app_gunicorn(**(self.opciones | {"direccion":"192.168.1.12"})): pass
        with patch("taxiroute.servidor_gunicorn_experimental.verificar_historial_sqlite_vinculado",side_effect=ValueError):
            with self.assertRaises(ValueError):
                with abrir_app_gunicorn(**self.opciones): pass
        with abrir_app_gunicorn(**self.opciones): pass


class PruebasCicloGunicorn(unittest.TestCase):
    def test_inicializa_contexto_solo_al_cargar_worker_y_cierra_en_worker_exit(self):
        modulo=ModuleType("gunicorn.app.base")
        class BaseFicticia:
            def __init__(self):
                self.cfg=Mock();self.load_config()
        modulo.BaseApplication=BaseFicticia
        opciones={"escucha":"127.0.0.1","puerto":18772,"certificado":"cert-ficticio",
                  "clave":"clave-ficticia","aplicacion":{}}
        contexto=Mock(); app=Mock();contexto.__enter__=Mock(return_value=app);contexto.__exit__=Mock()
        with patch.dict(sys.modules,{"gunicorn":ModuleType("gunicorn"),"gunicorn.app":ModuleType("gunicorn.app"),
                                     "gunicorn.app.base":modulo}), \
             patch("taxiroute.servidor_gunicorn_experimental.abrir_app_gunicorn",return_value=contexto) as abrir:
            servidor=crear_servidor_gunicorn(opciones)
            abrir.assert_not_called()
            self.assertIs(servidor.load(),app)
            abrir.assert_called_once_with()
            with self.assertRaises(RuntimeError):servidor.load()
            servidor._salida_worker(None,None)
            contexto.__exit__.assert_called_once_with(None,None,None)
            with self.assertRaises(RuntimeError):servidor._numero_workers(None,2,1)
            valores=dict(call.args for call in servidor.cfg.set.call_args_list)
            self.assertEqual((valores["workers"],valores["worker_class"],valores["threads"]),(1,"gthread",4))
            self.assertFalse(valores["preload_app"])
            self.assertEqual(valores["secure_scheme_headers"],{})


if __name__ == "__main__":
    unittest.main()
