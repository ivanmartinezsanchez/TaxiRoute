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
let recuperacionCompletada = false;
let cuerpoRecuperacion = null;
let revisionEnviada = null;
let consultasRevision = 0;
let cuerpoIntencion = null;
let eventosCarrera = [];
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
  document: {body:{dataset:{durable: ["reintento", "error_cierre", "recuperar", "intencion_sola", "intencion_punto", "confirmar_intencion", "inicio_admin", "inicio_conductor"].includes(escenario) ? "true" : "false", persistente: ["recuperar", "intencion_sola", "intencion_punto", "confirmar_intencion", "inicio_admin", "inicio_conductor"].includes(escenario) ? "true" : "false", rol: escenario === "inicio_admin" ? "administrador" : "conductor"}}, querySelector: elemento, createElement: () => ({textContent: "", append() {}}), dispatchEvent(evento) {eventosCarrera.push(evento.detail.activa);}},
  CustomEvent: class {constructor(tipo, opciones) {this.type = tipo; this.detail = opciones.detail;}},
  fetch: async (ruta, opciones = {}) => {
    if (ruta === "/api/recuperacion/intencion/confirmar") { cuerpoIntencion = JSON.parse(opciones.body); return respuesta(200, {mensaje:"Revisión registrada."}); }
    if (ruta === "/api/carrera/cierre-pendiente" && ["confirmar_intencion", "inicio_admin", "inicio_conductor"].includes(escenario)) return respuesta(200, {pendiente:null});
    if (ruta === "/api/recuperacion/pendiente" && escenario === "confirmar_intencion") return respuesta(200, {pendiente:{inicio:"2026-11-09T12:00:00+00:00", ultimo_control:"2026-11-09T12:00:05+00:00", estado_vehiculo:"parado", estado:"activa"}, intencion:{accion:"mover", huella:"b".repeat(64)}});
    if (ruta === "/api/recuperacion/pendiente" && ["inicio_admin", "inicio_conductor"].includes(escenario)) return respuesta(200, {pendiente:null, intencion:{accion:"iniciar", huella:"c".repeat(64)}});
    if (ruta === "/api/carrera/revision-recuperacion") consultasRevision += 1;
    if (ruta === "/api/carrera/cierre-pendiente" && ["intencion_sola", "intencion_punto"].includes(escenario)) return respuesta(200, {pendiente:null});
    if (ruta === "/api/recuperacion/pendiente" && escenario === "intencion_sola") return respuesta(200, {pendiente:null, intencion:{accion:"iniciar"}});
    if (ruta === "/api/recuperacion/pendiente" && escenario === "intencion_punto") return respuesta(200, {pendiente:{inicio:"2026-11-09T12:00:00+00:00", ultimo_control:"2026-11-09T12:00:05+00:00", estado_vehiculo:"parado", estado:"activa"}, intencion:{accion:"mover"}});
    if (escenario === "error_accion" && opciones.method === "POST") throw new Error("Tiempo agotado");
    if (ruta === "/api/carrera/confirmar-recuperacion" && escenario === "recuperar") {
      cuerpoRecuperacion = JSON.parse(opciones.body); recuperacionCompletada = true;
      return respuesta(200, {activa:false, estado:"sin carrera activa", importe:"0.42", pendiente:false, revision:2, mensaje:"Carrera recuperada."});
    }
    if (ruta === "/api/carrera/revision-recuperacion" && escenario === "recuperar") return respuesta(200, {pendiente:{
      inicio:"2026-11-09T12:00:00+00:00", ultimo_control:"2026-11-09T12:00:05+00:00",
      estado_vehiculo:"parado", estado:"activa", revision_disponible:true, revision_recuperacion:1, importe_recuperado:"0.42", huella:"a".repeat(64)}});
    if (ruta === "/api/carrera/cierre-pendiente" && escenario === "recuperar") return respuesta(200, {pendiente:null});
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
    if (["revision", "intencion_sola", "intencion_punto", "confirmar_intencion", "inicio_admin", "inicio_conductor"].includes(escenario) || (escenario === "reintento" && !reintentoCompletado) || (escenario === "error_cierre" && cierreFallido) || (escenario === "recuperar" && !recuperacionCompletada)) {
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
          revisionError: elemento("#revision-error").textContent,
          tarifasActuales: elemento("#tarifas-actuales").textContent,
          tarifasNuevas: elemento("#tarifas-nuevas").textContent,
          reintentoOculto: elemento("#reintentar").hidden,
          reintentoDeshabilitado: elemento("#reintentar").disabled,
          recuperacionOculta: elemento("#opciones-recuperacion").hidden,
          recuperacionDeshabilitada: elemento("#finalizar-recuperada").disabled,
          intencionOculta: elemento("#opciones-intencion").hidden,
          intencionDeshabilitada: elemento("#revisar-intencion").disabled,
          intencionTexto: elemento("#texto-confirmar-intencion").textContent,
          importeRecuperado: elemento("#importe-recuperado").textContent};
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
  if (escenario === "recuperar") {
    await vm.runInContext("confirmarRecuperacion()", contexto);
    if (cuerpoRecuperacion !== null) throw new Error("La confirmación no debe enviarse sin aceptar");
    elemento("#confirmar-recuperacion").checked = true;
    vm.runInContext("controles()", contexto);
    await vm.runInContext("confirmarRecuperacion()", contexto);
  }
  if (["confirmar_intencion", "inicio_admin", "inicio_conductor"].includes(escenario)) {
    await vm.runInContext("confirmarIntencion()", contexto);
    if (cuerpoIntencion !== null) throw new Error("La intención requiere aceptación");
    elemento("#confirmar-intencion").checked = true;
    vm.runInContext("controles()", contexto);
    await vm.runInContext("confirmarIntencion()", contexto);
  }
  if (escenario === "reintento") await vm.runInContext("actuar('/api/carrera/reintentar')", contexto);
  if (escenario === "error_cierre") { await vm.runInContext("actuar('/api/carrera/finalizar')", contexto); await new Promise((resolve) => setImmediate(resolve)); }
  const formato = escenario === "formato" ? vm.runInContext(`({fecha:fechaHistorial("2026-11-10T14:00:46.386516+00:00"), invalida:fechaHistorial("fecha-invalida"), duracion:duracionHistorial("10.6654620"), duracionInvalida:duracionHistorial("error")})`, contexto) : null;
  process.stdout.write(JSON.stringify({primero, durante, despues: estado(), revisionEnviada, cuerpoRecuperacion, cuerpoIntencion, eventosCarrera, consultasRevision, formato}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(NODE, "Node.js no está disponible")
class PruebasTaximetroJS(unittest.TestCase):
    def ejecutar_escenario(self, escenario):
        resultado = subprocess.run(
            [NODE, "-e", _RUNNER, str(SCRIPT), escenario],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return json.loads(resultado.stdout)

    def test_historial_formatea_fecha_y_duracion_sin_ocultar_datos_invalidos(self):
        formato = self.ejecutar_escenario("formato")["formato"]
        self.assertRegex(formato["fecha"], r"\d{2}/\d{2}/\d{4}")
        self.assertNotIn("T14:00", formato["fecha"])
        self.assertEqual(formato["duracion"], "10,67")
        self.assertEqual(formato["invalida"], "fecha-invalida")
        self.assertEqual(formato["duracionInvalida"], "error")

    def test_notifica_si_la_carrera_esta_activa(self):
        resultado = self.ejecutar_escenario("tarifas")
        self.assertEqual(resultado["eventosCarrera"], [True])

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

    def test_recuperar_exige_confirmacion_y_envia_punto_mostrado(self):
        resultado = self.ejecutar_escenario("recuperar")
        self.assertFalse(resultado["primero"]["recuperacionOculta"])
        self.assertTrue(resultado["primero"]["recuperacionDeshabilitada"])
        self.assertEqual(resultado["primero"]["importeRecuperado"], "0,42")
        self.assertEqual(resultado["cuerpoRecuperacion"], {"revision":1, "ultimo_control":"2026-11-09T12:00:05+00:00", "huella":"a" * 64, "confirmar":True})
        self.assertTrue(resultado["despues"]["recuperacionOculta"])

    def test_intencion_pendiente_sin_punto_explica_incertidumbre_y_bloquea(self):
        resultado = self.ejecutar_escenario("intencion_sola")
        estado = resultado["primero"]
        self.assertEqual(estado["revisionSituacion"], "Acción pendiente de revisión")
        self.assertIn("iniciar la carrera", estado["revisionError"])
        self.assertIn("No hay un punto fiable", estado["revisionError"])
        self.assertTrue(estado["recuperacionOculta"])
        self.assertTrue(estado["iniciar"])
        self.assertEqual(resultado["consultasRevision"], 0)

    def test_intencion_tiene_prioridad_sobre_punto_existente(self):
        resultado = self.ejecutar_escenario("intencion_punto")
        estado = resultado["primero"]
        self.assertIn("poner el vehículo en movimiento", estado["revisionError"])
        self.assertEqual(estado["revisionEstado"], "parado")
        self.assertTrue(estado["recuperacionOculta"])
        self.assertTrue(estado["recuperacionDeshabilitada"])
        self.assertEqual(resultado["consultasRevision"], 0)

    def test_revisar_cambio_exige_casilla_y_envia_huella(self):
        resultado = self.ejecutar_escenario("confirmar_intencion")
        self.assertFalse(resultado["primero"]["intencionOculta"])
        self.assertTrue(resultado["primero"]["intencionDeshabilitada"])
        self.assertEqual(resultado["cuerpoIntencion"], {"huella": "b" * 64, "confirmar": True})

    def test_inicio_sin_punto_solo_muestra_control_a_admin(self):
        admin = self.ejecutar_escenario("inicio_admin")
        conductor = self.ejecutar_escenario("inicio_conductor")
        self.assertFalse(admin["primero"]["intencionOculta"])
        self.assertTrue(admin["primero"]["intencionDeshabilitada"])
        self.assertIn("no hay un punto ni un importe fiable", admin["primero"]["intencionTexto"])
        self.assertEqual(admin["cuerpoIntencion"], {"huella": "c" * 64, "confirmar": True})
        self.assertTrue(conductor["primero"]["intencionOculta"])
        self.assertIsNone(conductor["cuerpoIntencion"])
