"""Filtro visual de historial con reloj y zona horaria controlados."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "taxiroute" / "static" / "taximetro_seguro.js"

RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const escenario = process.argv[2];
let ahora = "2026-03-29T22:30:00Z";
const DateReal = Date;
class Reloj extends DateReal {
  constructor(...args) { super(...(args.length ? args : [ahora])); }
  static now() { return new DateReal(ahora).getTime(); }
}
const elementos = new Map(), intervalos = new Map();
function nodo() {
  return {value:"", textContent:"", hidden:false, disabled:false, checked:false,
    children:[], listeners:{}, addEventListener(tipo, fn) {this.listeners[tipo] = fn;},
    replaceChildren() {this.children = [];}, append(hijo) {this.children.push(hijo);}};
}
function elemento(selector) {
  if (!elementos.has(selector)) elementos.set(selector, nodo());
  return elementos.get(selector);
}
elemento("#periodo-historial-propio").value = "todas";
const carrera = (inicio, fin) => ({inicio, fin, duracion_segundos:"10", importe:"1.50"});
let carreras = [];
if (escenario === "cambio_horario") {
  ahora = "2026-10-25T01:45:00Z";
  carreras = [carrera("dia_anterior", "2026-10-24T21:59:59Z"),
    carrera("medianoche", "2026-10-24T22:00:00Z"),
    carrera("hora_antes", "2026-10-25T02:30:00+02:00"),
    carrera("hora_despues", "2026-10-25T02:30:00+01:00")];
}
if (escenario === "limite") carreras = [
  carrera("anterior", "2026-03-29T21:59:59+00:00"),
  carrera("medianoche", "2026-03-29T22:00:00+00:00"),
  carrera("otro_huso", "2026-03-30T01:00:00+02:00"),
  carrera("sin_zona", "2026-03-30T01:00:00"),
  carrera("fecha_imposible", "2026-02-30T01:00:00+00:00"),
  null,
];
if (escenario === "cambio_dia") carreras = [carrera("de_ayer", "2026-03-29T21:00:00+00:00"), carrera("de_hoy", "2026-03-29T23:00:00+00:00")];
let llamadas = 0, diferidas = [];
const respuesta = (estado, datos) => ({status:estado, ok:estado >= 200 && estado < 300, json:async () => datos});
const ctx = vm.createContext({
  Date:Reloj,
  document:{body:{dataset:{durable:"false",persistente:"false"}}, querySelector:elemento, createElement:nodo, dispatchEvent() {}},
  CustomEvent:class {constructor(tipo, opciones) {this.detail=opciones.detail;}},
  fetch:async ruta => {
    if (ruta === "/api/historial") {
      llamadas += 1;
      if (escenario === "solapadas") return await new Promise(resolve => diferidas.push(resolve));
      return respuesta(200, {carreras});
    }
    if (ruta === "/api/carrera") return respuesta(200, {activa:false, estado:"sin carrera activa", importe:null, pendiente:false, revision:0});
    if (ruta === "/api/tarifas") return respuesta(200, {tarifas:{parado:"0.02", "en movimiento":"0.05"}});
    throw Error("Ruta inesperada " + ruta);
  },
  AbortSignal:{timeout(){return {};}}, setInterval(fn, ms){intervalos.set(ms, fn);},
  window:{location:{assign(){}}}, console,
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const espera = () => new Promise(resolve => setImmediate(resolve));
const filas = () => elemento("#historial").children.map(fila => fila.children.map(celda => celda.textContent));
const estado = () => ({filas:filas(), aviso:elemento("#aviso-historial").textContent, llamadas});
(async () => {
  await espera();
  if (escenario === "solapadas") {
    elemento("#periodo-historial-propio").value="hoy";
    const segunda = elemento("#periodo-historial-propio").listeners.change();
    diferidas[1](respuesta(200,{carreras:[carrera("reciente","2026-03-29T22:30:00+00:00")]}));
    await segunda;
    diferidas[0](respuesta(200,{carreras:[carrera("respuesta_obsoleta","2026-03-29T22:30:00+00:00")]}));
    await espera();
    process.stdout.write(JSON.stringify({final:estado()}));
    return;
  }
  const todas = estado();
  elemento("#periodo-historial-propio").value="hoy";
  await elemento("#periodo-historial-propio").listeners.change();
  const hoy = estado();
  if (escenario === "cambio_dia") {
    ahora = "2026-03-30T22:30:00Z";
    intervalos.get(60000)();
    await espera();
  } else {
    elemento("#periodo-historial-propio").value="todas";
    await elemento("#periodo-historial-propio").listeners.change();
  }
  process.stdout.write(JSON.stringify({todas, hoy, final:estado()}));
})().catch(error => { console.error(error); process.exitCode=1; });
"""


@unittest.skipUnless(NODE, "Node.js no está disponible")
class PruebasHistorialHoy(unittest.TestCase):
    def ejecutar(self, escenario):
        resultado = subprocess.run([NODE, "-e", RUNNER, str(SCRIPT), escenario],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env={**os.environ, "TZ": "Europe/Madrid"}, check=False)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return json.loads(resultado.stdout)

    def test_hoy_usa_fin_local_y_conserva_fechas_no_validas(self):
        resultado = self.ejecutar("limite")
        self.assertEqual([fila[0] for fila in resultado["hoy"]["filas"]],
                         ["medianoche", "otro_huso", "sin_zona", "fecha_imposible", "Registro no válido"])
        self.assertIn("3 carreras con fecha de finalización no válida", resultado["hoy"]["aviso"])
        self.assertEqual(len(resultado["todas"]["filas"]), 6)
        self.assertEqual(len(resultado["final"]["filas"]), 6)

    def test_vacio_informa_sin_inventar_carreras(self):
        resultado = self.ejecutar("vacio")
        self.assertEqual(resultado["hoy"]["filas"], [])
        self.assertEqual(resultado["hoy"]["aviso"], "Todavía no hay carreras guardadas.")

    def test_dia_con_cambio_de_hora_incluye_las_dos_horas_repetidas(self):
        resultado = self.ejecutar("cambio_horario")
        self.assertEqual([fila[0] for fila in resultado["hoy"]["filas"]],
                         ["medianoche", "hora_antes", "hora_despues"])

    def test_medianoche_actualiza_el_filtro(self):
        resultado = self.ejecutar("cambio_dia")
        self.assertEqual([fila[0] for fila in resultado["hoy"]["filas"]], ["de_hoy"])
        self.assertEqual(resultado["final"]["filas"], [])
        self.assertIn("No hay carreras finalizadas hoy", resultado["final"]["aviso"])
        self.assertEqual(resultado["final"]["llamadas"], 3)

    def test_respuesta_antigua_no_sobrescribe_filtro_actual(self):
        resultado = self.ejecutar("solapadas")
        self.assertEqual([fila[0] for fila in resultado["final"]["filas"]], ["reciente"])
