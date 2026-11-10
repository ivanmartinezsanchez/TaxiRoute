"""Pruebas de la revisión de cuentas con DOM y respuestas simulados."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "taxiroute" / "static" / "administracion.js"
RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const elementos = new Map(), llamadas = [], escenario = process.argv[2];
class Elemento {
  constructor(tipo) { this.tipo=tipo; this.children=[]; this.events={}; this.textContent=''; this.hidden=false; this.disabled=false; this.checked=false; }
  append(...hijos) { this.children.push(...hijos); }
  replaceChildren(...hijos) { this.children=hijos; }
  addEventListener(tipo, f) { this.events[tipo]=f; }
  setAttribute() {}
  querySelector() { return new Elemento('button'); }
}
function elemento(selector) { if (selector === '#periodo-historial') return null; if (!elementos.has(selector)) elementos.set(selector,new Elemento('nodo')); return elementos.get(selector); }
function buscar(nodo, predicado) { if (predicado(nodo)) return nodo; for (const hijo of nodo.children) { const hallado=buscar(hijo,predicado); if (hallado) return hallado; } return null; }
const respuesta = (status, datos) => ({status,ok:status>=200 && status<300,json:async()=>datos});
const contexto = vm.createContext({
  document:{body:{dataset:{durable:escenario==='sin_durable'?'false':'true', persistente:escenario==='sin_persistencia'?'false':'true'}}, querySelector:elemento,
    createElement:(tipo)=>new Elemento(tipo), querySelectorAll:()=>[]},
  fetch:async (ruta, opciones={})=>{
    llamadas.push({ruta,body:opciones.body});
    if(ruta==='/api/administracion/conductores') return respuesta(200,{conductores:[{id:'usuario1',usuario:'ana',nombre_visible:'Ana',habilitado:true,bloqueado:false}]});
    if(ruta==='/api/tarifas') return respuesta(200,{tarifas:{parado:'0.02','en movimiento':'0.05'}});
    if(ruta.endsWith('/intencion')) return respuesta(200,{intencion:{accion:'iniciar',huella:escenario==='huella_mala'?'no-valida':'a'.repeat(64)},pendiente:null});
    if(ruta.endsWith('/intencion/resolver')) return respuesta(200,{resultado:'inicio_revisado',mensaje:'Revisión registrada.'});
    throw new Error('Ruta inesperada: '+ruta);
  }, AbortSignal:{timeout(){return {}}}, window:{location:{assign(){}}}, console
});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),contexto);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  const raiz=elemento('#cuentas');
  const consultar=buscar(raiz,x=>x.textContent==='Consultar acción pendiente');
  if(['sin_durable','sin_persistencia'].includes(escenario)) { process.stdout.write(JSON.stringify({existe:!!consultar,llamadas})); return; }
  if(!consultar) throw new Error('No se encontró el botón');
  await consultar.events.click();
  const confirmar=buscar(raiz,x=>x.textContent==='Confirmar revisión');
  const checkbox=buscar(raiz,x=>x.tipo==='input' && x.type==='checkbox');
  const aceptar=buscar(raiz,x=>x.tipo==='span' && x.textContent.includes('revisado lo ocurrido'));
  if(!confirmar || !checkbox) throw new Error('Faltan controles');
  const inicialDeshabilitado=confirmar.disabled;
  await confirmar.events.click();
  const antes=llamadas.filter(x=>x.ruta.endsWith('/resolver')).length;
  checkbox.checked=true; checkbox.events.change();
  const despuesDeshabilitado=confirmar.disabled;
  await confirmar.events.click();
  process.stdout.write(JSON.stringify({inicialDeshabilitado,despuesDeshabilitado,antes,llamadas,texto:aceptar?.textContent}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""


@unittest.skipUnless(NODE, "Node.js no está disponible")
class PruebasRevisionAdministracionJS(unittest.TestCase):
    def ejecutar(self, escenario):
        resultado = subprocess.run([NODE, "-e", RUNNER, str(SCRIPT), escenario],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return json.loads(resultado.stdout)

    def test_inicio_exige_revision_de_conductor_y_casilla(self):
        resultado = self.ejecutar("inicio")
        self.assertTrue(resultado["inicialDeshabilitado"])
        self.assertFalse(resultado["despuesDeshabilitado"])
        self.assertEqual(resultado["antes"], 0)
        self.assertIn("revisado lo ocurrido con el conductor", resultado["texto"])
        envio = [x for x in resultado["llamadas"] if x["ruta"].endswith("/resolver")]
        self.assertEqual(len(envio), 1)
        self.assertEqual(json.loads(envio[0]["body"]), {"huella":"a" * 64, "confirmar":True})

    def test_huella_invalida_no_habilita_confirmacion(self):
        resultado = self.ejecutar("huella_mala")
        self.assertTrue(resultado["inicialDeshabilitado"])
        self.assertTrue(resultado["despuesDeshabilitado"])
        self.assertFalse(any(x["ruta"].endswith("/resolver") for x in resultado["llamadas"]))

    def test_modo_no_durable_no_expone_accion(self):
        for modo in ("sin_durable", "sin_persistencia"):
            with self.subTest(modo=modo):
                resultado = self.ejecutar(modo)
                self.assertFalse(resultado["existe"])
                self.assertFalse(any("/api/recuperacion/" in x["ruta"] for x in resultado["llamadas"]))
