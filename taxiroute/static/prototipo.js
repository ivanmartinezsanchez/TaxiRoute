const token = document.querySelector('meta[name="taxiroute-token"]').content;
const botones = [...document.querySelectorAll("[data-accion]")];
const mensaje = document.querySelector("#mensaje");
const conexion = document.querySelector("#conexion");
let actual = null;
let conectado = false;
let ocupado = false;
let leyendo = false;

function controles() {
  for (const boton of botones) {
    const accion = boton.dataset.accion;
    boton.disabled = !conectado || ocupado || !actual ||
      (accion === "iniciar" ? actual.activa : !actual.activa) ||
      (accion === "mover" && actual.estado === "en movimiento") ||
      (accion === "parar" && actual.estado === "parado");
  }
}
function mostrar(datos) {
  actual = datos;
  document.querySelector("#importe").textContent = datos.importe.replace(".", ",");
  document.querySelector("#estado").textContent = datos.activa
    ? (datos.estado === "parado" ? "Parado" : "En movimiento")
    : (datos.finalizada ? "Carrera finalizada" : "Listo para comenzar");
  document.querySelector("#etiqueta-importe").textContent = datos.finalizada ? "Total de la prueba" : "Importe provisional";
  document.querySelector("#parar").setAttribute("aria-pressed", String(datos.activa && datos.estado === "parado"));
  document.querySelector("#mover").setAttribute("aria-pressed", String(datos.activa && datos.estado === "en movimiento"));
  controles();
}
function desconectar() {
  conectado = false;
  conexion.textContent = "Sin conexión";
  mensaje.textContent = "No se puede confirmar el estado. El importe mostrado puede estar desactualizado. Comprueba el servicio Python; no se reintenta ninguna acción automáticamente.";
  controles();
}
async function solicitar(url, opciones = {}) {
  const respuesta = await fetch(url, {...opciones, cache: "no-store", signal: AbortSignal.timeout(5000)});
  const datos = await respuesta.json();
  if (!respuesta.ok) throw new Error(datos.error || "El servicio ha rechazado la petición.");
  return datos;
}
async function consultar() {
  if (ocupado || leyendo) return;
  leyendo = true;
  const revisionAlComenzar = actual?.revision;
  try {
    const datos = await solicitar("/prototipo/estado");
    // Una consulta iniciada antes de pulsar no debe pisar la respuesta de la acción.
    if (ocupado || (actual && actual.revision !== revisionAlComenzar)) return;
    const recuperado = !conectado;
    conectado = true;
    conexion.textContent = "Conexión local";
    mostrar(datos);
    if (recuperado) mensaje.textContent = "Conexión disponible. Revisa el estado antes de continuar.";
  } catch {
    if (!ocupado) desconectar();
  } finally { leyendo = false; }
}
for (const boton of botones) {
  boton.addEventListener("click", async () => {
    if (ocupado || !conectado || !actual) return;
    ocupado = true;
    controles();
    try {
      const datos = await solicitar("/prototipo/accion", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-TaxiRoute-Token": token},
        body: JSON.stringify({accion: boton.dataset.accion, revision: actual.revision})
      });
      mostrar(datos);
      mensaje.textContent = datos.mensaje;
    } catch (error) {
      desconectar();
      mensaje.textContent = error.message + " Se consultará el estado antes de permitir otra acción.";
    } finally {
      ocupado = false;
      controles();
      consultar();
    }
  });
}
consultar();
setInterval(consultar, 1000);
