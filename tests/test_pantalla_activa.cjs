const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const codigo = fs.readFileSync(path.join(__dirname, '..', 'taxiroute', 'static', 'pantalla_activa.js'), 'utf8');
const espera = () => new Promise(resolve => setImmediate(resolve));

function entorno(request) {
  const listeners = {};
  const opcion = {checked: false, addEventListener: (nombre, fn) => listeners[nombre] = fn};
  const estado = {textContent: ''};
  const document = {visibilityState: 'visible', querySelector: s => s === '#mantener-pantalla' ? opcion : estado,
    addEventListener: (nombre, fn) => listeners[nombre] = fn};
  vm.runInNewContext(codigo, {document, navigator: request ? {wakeLock: {request}} : {}});
  return {listeners, opcion, estado, document};
}
function bloqueo() {
  return {released: false, addEventListener: function(_, fn) {this.alLiberar = fn;},
    release: async function() {this.released = true; this.alLiberar?.();}};
}
test('activa durante una carrera y libera al finalizar', async () => {
  const b = bloqueo(); let llamadas = 0;
  const e = entorno(async () => {llamadas++; return b;});
  e.opcion.checked = true; e.listeners.change(); await espera();
  assert.equal(llamadas, 0);
  e.listeners['taxiroute:carrera']({detail:{activa:true}}); await espera();
  assert.equal(llamadas, 1); assert.match(e.estado.textContent, /activa solicitada/);
  e.listeners['taxiroute:carrera']({detail:{activa:false}}); await espera();
  assert.equal(b.released, true);
});
test('si no está disponible lo explica', async () => {
  const e = entorno(null); e.opcion.checked = true;
  e.listeners['taxiroute:carrera']({detail:{activa:true}}); await espera();
  assert.match(e.estado.textContent, /no permite/);
});
test('si el navegador rechaza la solicitud muestra el fallo', async () => {
  const e = entorno(async () => {throw new Error('denegado');});
  e.opcion.checked = true; e.listeners['taxiroute:carrera']({detail:{activa:true}}); await espera();
  assert.match(e.estado.textContent, /No se ha podido/);
});
test('si el usuario desactiva antes de recibir permiso libera la petición tardía', async () => {
  let resolver; const b = bloqueo();
  const e = entorno(() => new Promise(resolve => {resolver = resolve;}));
  e.opcion.checked = true; e.listeners['taxiroute:carrera']({detail:{activa:true}});
  e.opcion.checked = false; e.listeners.change(); resolver(b); await espera(); await espera();
  assert.equal(b.released, true); assert.match(e.estado.textContent, /desactivada/);
});
test('al ocultar y volver a la pestaña vuelve a solicitar el bloqueo', async () => {
  const bloques = []; const e = entorno(async () => {const b=bloqueo(); bloques.push(b); return b;});
  e.opcion.checked = true; e.listeners['taxiroute:carrera']({detail:{activa:true}}); await espera();
  e.document.visibilityState='hidden'; e.listeners.visibilitychange(); await espera();
  assert.equal(bloques[0].released, true);
  e.document.visibilityState='visible'; e.listeners.visibilitychange(); await espera();
  assert.equal(bloques.length, 2); assert.equal(bloques[1].released, false);
});
