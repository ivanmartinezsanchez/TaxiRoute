"""Comportamiento del taxímetro JavaScript con respuestas y DOM simulados.

No inicia navegador ni servidor. Requiere Node.js para ejecutar el script real.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "taxiroute" / "static" / "taximetro_seguro.js"

_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");
const fichero = process.argv[1];
const escenario = process.argv[2];
const elementos = new Map();
let consultas = 0;
let resolverConsulta;
let reintentoCompletado = false;
let cierreFallido = false;
let revisionEnviada = null;
function elemento(selector) {
  if (!elementos.has(selector)) {
    elementos.set(selector, {
      textContent: "", hidden: false, disabled: false,
      addEventListener() {}, replaceChildren() {}, append() {}
    });
  }
  return elementos.get(selector);
}
const respuesta = (status, datos) => ({status, ok: status >= 200 && status < 300, json: async () => datos});
const contexto = vm.createContext({
  document: {body:{dataset:{durable: ["reintento", "error_cierre"].includes(escenario) ? "true" : "false"}}, querySelector: elemento, createElement: () => ({textContent: "", append() {}})},
  fetch: async (ruta, opciones = {}) => {
    if (escenario === "error_accion" && opciones.method === "POST") throw new Error("Tiempo agotado");
    if (ruta === "/api/carrera/finalizar" && escenario === "error_cierre") {
      cierreFallido = true;
      return respuesta(503, {error:"No se puede confirmar el resultado. No queda espacio en la unidad."});
    }
    if (ruta === "/api/carrera/reintentar" && escenario === "reintento") {
      revisionEnviada = JSON.parse(opciones.body).revision;
      reintentoCompletado = true;
      return respuesta(200, {activa:false, estado:"sin carrera activa", importe:"0.20", pendiente:false, revision:2, mensaje:"Carrera guardada."});
    }
    if (ruta === "/api/carrera/cierre-pendiente" && ["reintento", "error_cierre"].includes(escenario)) return respuesta(200, {pendiente:{
      inicio:"2026-11-09T12:00:00+00:00", ultimo_control:"2026-11-09T12:00:05+00:00",
      estado_vehiculo:"parado", estado:"guardada", reintento_disponible:true, revision_reintento:1}});
    if (ruta === "/api/historial") return respuesta(200, {carreras: []});
    if (ruta === "/api/tarifas") return respuesta(200, {tarifas:{parado:"0.03", "en movimiento":"0.06"}});
    if (ruta === "/api/carrera/tarifas") return respuesta(200, {revision:1, tarifas:{parado:"0.02", "en movimiento":"0.05"}});
    if (ruta === "/api/recuperacion/pendiente") return respuesta(200, {pendiente: {
      inicio:"2026-11-09T12:00:00+00:00", ultimo_control:"2026-11-09T12:00:05+00:00",
      estado_vehiculo:"parado", estado:"revision_pendiente"}});
    if (ruta !== "/api/carrera") throw new Error("Ruta inesperada: " + ruta);
    consultas += 1;
    if (escenario === "error_accion" && consultas === 2) {
      await new Promise((resolve) => { resolverConsulta = resolve; });
    }
    if (escenario === "error_cierre" && !cierreFallido) return respuesta(200, {activa:true, estado:"parado", importe:"0.20", pendiente:false, revision:1});
    if (escenario === "tarifas") return respuesta(200, {activa:true, estado:"parado", importe:"0.20", pendiente:false, revision:1});
    if (escenario === "revision" || (escenario === "reintento" && !reintentoCompletado) || (escenario === "error_cierre" && cierreFallido)) {
      return respuesta(409, {error: "La carrera necesita revisión antes de continuar."});
    }
    if (escenario === "reconexion" && consultas === 1) throw new Error("Red no disponible");
    return respuesta(200, {activa: false, estado: "sin carrera activa", importe: null,
                            pendiente: false, revision: 0});
  },
  AbortSignal: {timeout() { return {}; }},
  setInterval() {},
  window: {location: {assign() {}}},
  console,
});
vm.runInContext(fs.readFileSync(fichero, "utf8"), contexto, {filename: fichero});
function estado() {
  return {conexion: elemento("#conexion").textContent,
          mensaje: elemento("#mensaje").textContent,
          iniciar: elemento("#iniciar").disabled,
          mover: elemento("#mover").disabled,
          parar: elemento("#parar").disabled,
          finalizar: elemento("#finalizar").disabled,
          panelOculto: elemento("#revision-pendiente").hidden,
          revisionInicio: elemento("#revision-inicio").textContent,
          revisionControl: elemento("#revision-control").textContent,
          revisionEstado: elemento("#revision-estado").textContent,
          revisionSituacion: elemento("#revision-situacion").textContent,
          tarifasActuales: elemento("#tarifas-actuales").textContent,
          tarifasNuevas: elemento("#tarifas-nuevas").textContent,
          reintentoOculto: elemento("#reintentar").hidden,
          reintentoDeshabilitado: elemento("#reintentar").disabled};
}
(async () => {
  await new Promise((resolve) => setImmediate(resolve));
  const primero = estado();
  if (escenario === "reconexion") await vm.runInContext("consultar()", contexto);
  let durante = null;
  if (escenario === "error_accion") {
    const accion = vm.runInContext("actuar('/api/carrera/iniciar')", contexto);
    await new Promise((resolve) => setImmediate(resolve));
    durante = estado();
    resolverConsulta();
    await accion;
  }
  if (escenario === "reintento") await vm.runInContext("actuar('/api/carrera/reintentar')", contexto);
  if (escenario === "error_cierre") { await vm.runInContext("actuar('/api/carrera/finalizar')", contexto); await new Promise((resolve) => setImmediate(resolve)); }
  process.stdout.write(JSON.stringify({primero, durante, despues: estado(), revisionEnviada}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(NODE, "Node.js no está disponible")
class PruebasTaximetroJS(unittest.TestCase):
    def ejecutar_escenario(self, escenario):
        resultado = subprocess.run(
            [NODE, "-e", _RUNNER, str(SCRIPT), escenario],
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return json.loads(resultado.stdout)

    def test_conflicto_de_revision_muestra_motivo_y_bloquea_acciones(self):
        estado = self.ejecutar_escenario("revision")["primero"]
        self.assertEqual(estado["conexion"], "Revisión necesaria")
        self.assertEqual(estado["mensaje"], "La carrera necesita revisión antes de continuar.")
        self.assertFalse(estado["panelOculto"])
        self.assertEqual(estado["revisionInicio"], "2026-11-09T12:00:00+00:00")
        self.assertEqual(estado["revisionControl"], "2026-11-09T12:00:05+00:00")
        self.assertEqual(estado["revisionEstado"], "parado")
        self.assertEqual(estado["revisionSituacion"], "Pendiente de revisión")
        for nombre in ("iniciar", "mover", "parar", "finalizar"):
            with self.subTest(boton=nombre):
                self.assertTrue(estado[nombre])

    def test_al_recuperar_conexion_se_retira_el_aviso_transitorio(self):
        resultado = self.ejecutar_escenario("reconexion")
        self.assertEqual(resultado["primero"]["conexion"], "Estado no disponible")
        self.assertIn("No se puede confirmar el estado", resultado["primero"]["mensaje"])
        self.assertEqual(resultado["despues"]["conexion"], "Conectado")
        self.assertNotIn("No se puede confirmar el estado", resultado["despues"]["mensaje"])
        self.assertFalse(resultado["despues"]["iniciar"])
    def test_error_de_accion_bloquea_botones_hasta_consultar_estado(self):
        resultado = self.ejecutar_escenario("error_accion")
        self.assertTrue(resultado["durante"]["iniciar"])
        self.assertIn("podría seguir procesándose", resultado["durante"]["mensaje"])
        self.assertFalse(resultado["despues"]["iniciar"])

    def test_muestra_tarifas_actuales_y_de_nuevas_carreras_por_separado(self):
        estado = self.ejecutar_escenario("tarifas")["primero"]
        self.assertIn("0,02 €/s", estado["tarifasActuales"])
        self.assertIn("0,05 €/s", estado["tarifasActuales"])
        self.assertIn("0,03 €/s", estado["tarifasNuevas"])
        self.assertIn("0,06 €/s", estado["tarifasNuevas"])

    def test_reintento_durable_disponible_sin_estado_previo(self):
        resultado = self.ejecutar_escenario("reintento")
        self.assertFalse(resultado["primero"]["reintentoOculto"])
        self.assertFalse(resultado["primero"]["reintentoDeshabilitado"])
        self.assertEqual(resultado["revisionEnviada"], 1)
        self.assertTrue(resultado["despues"]["reintentoOculto"])

    def test_error_de_espacio_permanece_visible_al_mostrar_revision(self):
        resultado = self.ejecutar_escenario("error_cierre")
        self.assertIn("No queda espacio", resultado["despues"]["mensaje"])
        self.assertEqual(resultado["despues"]["conexion"], "Revisión necesaria")
        self.assertFalse(resultado["despues"]["reintentoDeshabilitado"])
