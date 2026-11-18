const $ = (selector) => document.querySelector(selector);
const token = $('meta[name="taxiroute-token"]').content;
const botones = [...document.querySelectorAll("[data-accion]")];
const mensaje = $("#mensaje");
const conexion = $("#conexion");
let actual = null, conectado = false, ocupado = false, leyendo = false;
let filtro = "hoy", consultaHistorial = 0, formularioInicializado = false;
let ultimoError = "";

function controles() {
  const bloqueo = !conectado || ocupado || !actual || actual.cerrada;
  for (const boton of botones) {
    const accion = boton.dataset.accion;
    let imposible = false;
    if (accion === "iniciar") imposible = actual?.activa || actual?.pendiente || !!actual?.error_tarifas;
    if (["mover", "parar", "finalizar"].includes(accion)) imposible = !actual?.activa;
    if (accion === "mover") imposible ||= actual?.estado === "en movimiento";
    if (accion === "parar") imposible ||= actual?.estado === "parado";
    if (accion === "reintentar") imposible = !actual?.pendiente;
    if (accion === "salir") imposible = actual?.activa || actual?.pendiente;
    boton.disabled = bloqueo || imposible;
  }
  $("#exportar").disabled = bloqueo || !actual?.pendiente;
  $("#guardar-tarifas").disabled = bloqueo;
}
const decimal = (valor) => String(valor).replace(".", ",");
function mostrar(datos) {
  const revisionAnterior = actual?.revision;
  actual = datos;
  $("#importe").textContent = decimal(datos.importe);
  $("#estado").textContent = datos.cerrada ? "Aplicación cerrada" : datos.pendiente
    ? "Pendiente de guardar" : datos.activa
    ? (datos.estado === "parado" ? "Parado" : "En movimiento")
    : (datos.finalizada ? "Carrera finalizada" : "Listo para comenzar");
  $("#etiqueta-importe").textContent = datos.finalizada ? "Total de la carrera" : "Importe provisional";
  $("#parar").setAttribute("aria-pressed", String(datos.activa && datos.estado === "parado"));
  $("#mover").setAttribute("aria-pressed", String(datos.activa && datos.estado === "en movimiento"));
  $("#recuperacion").hidden = !datos.pendiente;
  $("#titulo-tarifas").textContent = datos.activa ? "TARIFAS DE LA CARRERA ACTIVA" : "TARIFAS PARA LA PRÓXIMA CARRERA";
  $("#tarifa-parado").textContent = datos.tarifas ? decimal(datos.tarifas.parado) : "—";
  $("#tarifa-mover").textContent = datos.tarifas ? decimal(datos.tarifas["en movimiento"]) : "—";
  const siguientes = datos.tarifas_siguientes;
  $("#proximas-tarifas").textContent = siguientes
    ? "Próxima carrera: " + decimal(siguientes.parado) + " €/s parado y " + decimal(siguientes["en movimiento"]) + " €/s en movimiento."
    : "La configuración necesita corregirse antes de iniciar otra carrera.";
  if (!formularioInicializado && siguientes) {
    $("#precio-parado").value = siguientes.parado;
    $("#precio-mover").value = siguientes["en movimiento"];
    formularioInicializado = true;
  }
  $("#aviso-registro").textContent = datos.aviso_registro || "";
  if (datos.error_tarifas) $("#mensaje-tarifas").textContent = datos.error_tarifas;
  if (!ultimoError) mensaje.textContent = datos.mensaje;
  if (datos.cerrada) {
    conexion.textContent = "Servicio cerrado";
    mensaje.textContent = "TaxiRoute cerrado. Puedes cerrar esta pestaña y arrancarlo de nuevo desde la terminal.";
  }
  controles();
  if (revisionAnterior !== datos.revision && !datos.cerrada) cargarHistorial();
}
function desconectar() {
  conectado = false;
  conexion.textContent = "Sin conexión";
  mensaje.textContent = "No se puede confirmar el estado. El importe mostrado puede estar desactualizado. Comprueba el servicio Python; no se repiten las acciones automáticamente.";
  controles();
}
async function solicitar(url, opciones = {}) {
  const respuesta = await fetch(url, {...opciones, cache: "no-store", signal: AbortSignal.timeout(5000)});
  let datos;
  try { datos = await respuesta.json(); }
  catch { throw new Error("Respuesta no válida. Si reiniciaste Python, recarga la página."); }
  if (!respuesta.ok) throw new Error(datos.error || "Petición rechazada. Recarga si reiniciaste el servicio.");
  return datos;
}
async function consultar() {
  if (ocupado || leyendo || actual?.cerrada) return;
  leyendo = true;
  const revisionAlComenzar = actual?.revision;
  try {
    const datos = await solicitar("/prototipo/estado");
    if (ocupado || (actual && actual.revision !== revisionAlComenzar)) return;
    conectado = true;
    conexion.textContent = "Conexión local";
    mostrar(datos);
  } catch { if (!ocupado) desconectar(); }
  finally { leyendo = false; }
}
async function actuar(accion, extras = {}) {
  if (ocupado || !conectado || !actual || actual.cerrada) return false;
  ocupado = true;
  ultimoError = "";
  controles();
  let correcto = false;
  try {
    const datos = await solicitar("/prototipo/accion", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-TaxiRoute-Token": token},
      body: JSON.stringify({accion, revision: actual.revision, ...extras})
    });
    mostrar(datos);
    correcto = true;
  } catch (error) {
    ultimoError = error.message;
    mensaje.textContent = ultimoError;
    conectado = false;
  } finally {
    ocupado = false;
    controles();
    consultar();
  }
  return correcto;
}
for (const boton of botones) boton.addEventListener("click", () => actuar(boton.dataset.accion));
$("#form-exportar").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  await actuar("exportar", {ruta: $("#ruta-copia").value.trim()});
});
$("#form-tarifas").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const tarifas = {
    parado: $("#precio-parado").value.trim().replace(",", "."),
    en_movimiento: $("#precio-mover").value.trim().replace(",", ".")
  };
  const correcto = await actuar("tarifas", {tarifas});
  $("#mensaje-tarifas").textContent = correcto
    ? "Tarifas guardadas para la siguiente carrera. La carrera activa no cambia."
    : ultimoError || "No se han podido guardar las tarifas.";
});
function agregarTexto(contenedor, etiqueta, texto) {
  const elemento = document.createElement(etiqueta);
  elemento.textContent = texto;
  contenedor.append(elemento);
}
function fechaLocal(valor) {
  const fecha = new Date(valor);
  return Number.isNaN(fecha.getTime()) ? valor : fecha.toLocaleString("es-ES");
}
async function cargarHistorial() {
  const turno = ++consultaHistorial;
  $("#mensaje-historial").textContent = "Consultando historial…";
  try {
    const datos = await solicitar("/prototipo/historial?filtro=" + filtro);
    if (turno !== consultaHistorial) return;
    $("#lista-historial").replaceChildren();
    for (const fila of datos.carreras.slice().reverse()) {
      const tarjeta = document.createElement("article");
      tarjeta.className = "carrera-historial";
      agregarTexto(tarjeta, "h3", decimal(fila.importe) + " €");
      agregarTexto(tarjeta, "p", "Inicio: " + fechaLocal(fila.inicio));
      agregarTexto(tarjeta, "p", "Fin: " + fechaLocal(fila.fin));
      agregarTexto(tarjeta, "p", "Duración: " + decimal(fila.duracion_segundos) + " s");
      agregarTexto(tarjeta, "p", "Parado: " + decimal(fila.segundos_parado) + " s · Movimiento: " + decimal(fila.segundos_movimiento) + " s");
      agregarTexto(tarjeta, "p", "Tarifas aplicadas: " + decimal(fila.tarifa_parado) + " / " + decimal(fila.tarifa_movimiento) + " €/s");
      agregarTexto(tarjeta, "p", "Identificador: " + fila.identificador);
      $("#lista-historial").append(tarjeta);
    }
    $("#mensaje-historial").textContent = datos.carreras.length
      ? datos.carreras.length + " carreras " + (filtro === "hoy" ? "finalizadas hoy." : "guardadas.")
      : (filtro === "hoy" ? "No hay carreras finalizadas hoy." : "No hay carreras guardadas.");
  } catch (error) {
    if (turno !== consultaHistorial) return;
    $("#lista-historial").replaceChildren();
    $("#mensaje-historial").textContent = error.message;
  }
}
for (const [id, valor] of [["historial-hoy", "hoy"], ["historial-todo", "todo"]]) {
  $("#" + id).addEventListener("click", () => {
    filtro = valor;
    $("#historial-hoy").setAttribute("aria-pressed", String(valor === "hoy"));
    $("#historial-todo").setAttribute("aria-pressed", String(valor === "todo"));
    cargarHistorial();
  });
}
$("#actualizar-historial").addEventListener("click", cargarHistorial);
$("#ver-registros").addEventListener("click", async () => {
  try {
    const datos = await solicitar("/prototipo/registros");
    $("#lista-registros").textContent = datos.eventos.map((evento) => JSON.stringify(evento)).join("\n") || "No hay eventos.";
  } catch (error) { $("#lista-registros").textContent = error.message; }
});
window.addEventListener("beforeunload", (evento) => {
  if (actual?.activa || actual?.pendiente) { evento.preventDefault(); evento.returnValue = ""; }
});
consultar();
setInterval(consultar, 1000);
