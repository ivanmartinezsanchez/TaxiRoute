"use strict";
const $ = (selector) => document.querySelector(selector);
let actual = null;
let ocupado = false;
let leyendo = false;
let conectado = false;
let requiereRevision = false;
let avisoConexion = false;
let ultimoError = "";
let consultandoPendiente = false;
let ultimaConsultaPendiente = 0;
let generacionRevision = 0;
let generacionHistorial = 0;
let revisionTarifas = null;
let generacionTarifas = 0;
let generacionTarifasNuevas = 0;
let revisionReintento = null;
let puntoRecuperacion = null;
let intencionRevision = null;

function controles() {
  const disponible = conectado && !ocupado && !requiereRevision && actual !== null;
  $("#iniciar").disabled = !disponible || actual.activa || actual.pendiente;
  $("#mover").disabled = !disponible || !actual.activa || actual.estado === "en movimiento";
  $("#parar").disabled = !disponible || !actual.activa || actual.estado === "parado";
  $("#finalizar").disabled = !disponible || !actual.activa;
  const reintentoDurable = requiereRevision && revisionReintento !== null;
  $("#reintentar").hidden = !actual?.pendiente && !reintentoDurable;
  $("#reintentar").disabled = reintentoDurable ? !conectado || ocupado : !disponible || !actual?.pendiente;
  $("#finalizar-recuperada").disabled = !conectado || ocupado || !requiereRevision || !puntoRecuperacion || !$("#confirmar-recuperacion").checked;
  $("#revisar-intencion").disabled = !conectado || ocupado || !requiereRevision || !intencionRevision || !$("#confirmar-intencion").checked;
}
function mostrar(datos) {
  actual = datos;
  document.dispatchEvent(new CustomEvent("taxiroute:carrera", {detail: {activa: datos.activa === true}}));
  if (revisionTarifas !== datos.revision) {
    revisionTarifas = datos.revision;
    void cargarTarifasActuales(datos.revision, datos.activa);
  }
  $("#estado").textContent = datos.estado;
  $("#importe").textContent = datos.importe === null ? "—" : String(datos.importe).replace(".", ",");
  $("#conexion").textContent = "Conectado";
  if (avisoConexion) {
    $("#mensaje").textContent = datos.pendiente ? "Hay una carrera pendiente de guardar. Usa «Reintentar guardado»." : "";
    avisoConexion = false;
  }
  if (!ultimoError && datos.mensaje) $("#mensaje").textContent = datos.mensaje;
  controles();
}
async function solicitar(ruta, opciones = {}) {
  const respuesta = await fetch(ruta, { ...opciones, cache: "no-store", signal: AbortSignal.timeout(5000) });
  if (respuesta.status === 401) { window.location.assign("/acceso"); throw new Error("Sesión no disponible."); }
  let datos;
  try { datos = await respuesta.json(); } catch { throw new Error("No se puede interpretar la respuesta."); }
  if (!respuesta.ok) {
    const error = new Error(datos.error || "No se pudo completar la acción.");
    error.status = respuesta.status;
    throw error;
  }
  return datos;
}
function textoTarifas(tarifas) {
  return `Parado: ${String(tarifas.parado).replace(".", ",")} €/s · En movimiento: ${String(tarifas["en movimiento"]).replace(".", ",")} €/s`;
}
async function cargarTarifasActuales(revision, activa) {
  const generacion = ++generacionTarifas;
  if (!activa) { $("#tarifas-actuales").textContent = "No hay una carrera activa."; return; }
  $("#tarifas-actuales").textContent = "Consultando…";
  try {
    const datos = await solicitar("/api/carrera/tarifas");
    if (generacion !== generacionTarifas || !actual || actual.revision !== revision) return;
    if (!datos.tarifas || datos.revision !== revision) { revisionTarifas = null; return; }
    $("#tarifas-actuales").textContent = textoTarifas(datos.tarifas);
  } catch (error) {
    if (generacion === generacionTarifas) { $("#tarifas-actuales").textContent = "No se pueden consultar las tarifas de esta carrera."; revisionTarifas = null; }
  }
}
async function cargarTarifasNuevas() {
  const generacion = ++generacionTarifasNuevas;
  try {
    const datos = await solicitar("/api/tarifas");
    if (generacion === generacionTarifasNuevas) $("#tarifas-nuevas").textContent = textoTarifas(datos.tarifas);
  } catch (error) {
    if (generacion === generacionTarifasNuevas) $("#tarifas-nuevas").textContent = "No se pueden consultar las tarifas para nuevas carreras.";
  }
}
async function mostrarPendiente() {
  if (consultandoPendiente || Date.now() - ultimaConsultaPendiente < 15000) return;
  consultandoPendiente = true; ultimaConsultaPendiente = Date.now();
  const generacion = generacionRevision;
  revisionReintento = null; controles();
  $("#revision-pendiente").hidden = false;
  for (const id of ["#revision-inicio", "#revision-control", "#revision-estado", "#revision-situacion"]) $(id).textContent = "—";
  $("#revision-error").textContent = "Consultando los datos guardados…";
  try {
    let datos = null;
    if (document.body?.dataset?.durable === "true") {
      datos = await solicitar("/api/carrera/cierre-pendiente");
    }
    if (!datos?.pendiente) datos = await solicitar("/api/recuperacion/pendiente");
    if (document.body?.dataset?.persistente === "true" && !datos?.pendiente?.reintento_disponible && !datos?.intencion) {
      try {
        const revision = await solicitar("/api/carrera/revision-recuperacion");
        if (revision?.pendiente?.revision_disponible) datos = revision;
      } catch (error) { /* Se conserva la información disponible para revisión. */ }
    }
    if (!requiereRevision || generacion !== generacionRevision) return;
    const pendiente = datos.pendiente;
    if (datos.intencion != null) {
      const acciones = {iniciar:"iniciar la carrera", mover:"poner el vehículo en movimiento", parar:"parar el vehículo"};
      const accion = acciones[datos.intencion.accion] || "realizar una acción";
      puntoRecuperacion = null; revisionReintento = null;
      $("#confirmar-recuperacion").checked = false;
      $("#opciones-recuperacion").hidden = true;
      $("#revision-situacion").textContent = "Acción pendiente de revisión";
      if (pendiente) {
        $("#revision-inicio").textContent = pendiente.inicio;
        $("#revision-control").textContent = pendiente.ultimo_control;
        $("#revision-estado").textContent = pendiente.estado_vehiculo;
      }
      const incertidumbre = pendiente
        ? "El estado y el tiempo posteriores al último punto son desconocidos."
        : "No hay un punto fiable para determinar el estado ni el tiempo.";
      $("#revision-error").textContent = `No se pudo confirmar si se completó la acción de ${accion}. ${incertidumbre} Consulta con administración.`;
      const huella = datos.intencion.huella;
      const propiaAdmin = datos.intencion.accion === "iniciar" && !pendiente && document.body?.dataset?.rol === "administrador";
      const cambio = ["mover", "parar"].includes(datos.intencion.accion) && pendiente != null;
      const valida = document.body?.dataset?.durable === "true" && document.body?.dataset?.persistente === "true" && (propiaAdmin || cambio)
        && typeof huella === "string" && /^[0-9a-f]{64}$/.test(huella);
      if (!valida || !intencionRevision || intencionRevision.huella !== huella)
        $("#confirmar-intencion").checked = false;
      intencionRevision = valida ? {huella, accion: datos.intencion.accion} : null;
      $("#opciones-intencion").hidden = !valida;
      if (propiaAdmin) {
        $("#ayuda-intencion").textContent = "Esta revisión no crea ninguna carrera ni calcula un importe para el intervalo sin datos.";
        $("#texto-confirmar-intencion").textContent = "He revisado mi situación y entiendo que no hay un punto ni un importe fiable.";
      } else if (cambio) {
        $("#ayuda-intencion").textContent = "La carrera seguirá bloqueada para revisar y cerrar el último punto fiable. No se cobrará automáticamente el intervalo desconocido.";
        $("#texto-confirmar-intencion").textContent = "He revisado los datos y entiendo que no se conoce el estado ni el tiempo posterior al último punto.";
      }
      controles();
      return;
    }
    intencionRevision = null; $("#opciones-intencion").hidden = true;
    $("#confirmar-intencion").checked = false;
    if (!pendiente) {
      puntoRecuperacion = null; $("#opciones-recuperacion").hidden = true; controles();
      $("#revision-error").textContent = "No se ha podido identificar la carrera pendiente. Consulta con administración.";
      return;
    }
    const situaciones = {activa:"Carrera interrumpida", revision_pendiente:"Pendiente de revisión",
                         guardado_pendiente:"Finalizada, pendiente de guardar",
                         guardada:"Guardada, pendiente de confirmar el cierre"};
    $("#revision-inicio").textContent = pendiente.inicio;
    $("#revision-control").textContent = pendiente.ultimo_control;
    $("#revision-estado").textContent = pendiente.estado_vehiculo;
    $("#revision-situacion").textContent = situaciones[pendiente.estado] || "Revisión necesaria";
    const revisionValida = pendiente.revision_disponible === true && Number.isSafeInteger(pendiente.revision_recuperacion)
      && typeof pendiente.ultimo_control === "string" && typeof pendiente.importe_recuperado === "string"
      && typeof pendiente.huella === "string" && /^[0-9a-f]{64}$/.test(pendiente.huella);
    const nuevoPunto = revisionValida ? {revision:pendiente.revision_recuperacion, instante:pendiente.ultimo_control, huella:pendiente.huella} : null;
    if (!nuevoPunto || !puntoRecuperacion || nuevoPunto.revision !== puntoRecuperacion.revision || nuevoPunto.instante !== puntoRecuperacion.instante || nuevoPunto.huella !== puntoRecuperacion.huella)
      $("#confirmar-recuperacion").checked = false;
    puntoRecuperacion = nuevoPunto;
    $("#opciones-recuperacion").hidden = !revisionValida;
    $("#importe-recuperado").textContent = revisionValida ? pendiente.importe_recuperado.replace(".", ",") : "—";
    revisionReintento = pendiente.reintento_disponible === true && Number.isSafeInteger(pendiente.revision_reintento)
      ? pendiente.revision_reintento : null;
    $("#revision-error").textContent = revisionReintento !== null
      ? "Puedes reintentar el guardado de esta carrera." : (puntoRecuperacion ? "Revisa los datos antes de confirmar." : "La revisión todavía no está disponible en esta demostración.");
    controles();
  } catch (error) {
    if (requiereRevision && generacion === generacionRevision) $("#revision-error").textContent = "No se pueden consultar los datos de recuperación. Inténtalo más tarde.";
  } finally { consultandoPendiente = false; }
}
async function consultar() {
  if (ocupado || leyendo) return;
  leyendo = true;
  try {
    const revisionAnterior = actual?.revision;
    const datos = await solicitar("/api/carrera");
    if (ocupado || (actual && revisionAnterior !== actual.revision)) return;
    conectado = true;
    if (requiereRevision) generacionRevision += 1;
    requiereRevision = false;
    revisionReintento = null; puntoRecuperacion = null;
    intencionRevision = null; $("#opciones-intencion").hidden = true; $("#confirmar-intencion").checked = false;
    $("#opciones-recuperacion").hidden = true;
    $("#revision-pendiente").hidden = true;
    ultimaConsultaPendiente = 0;
    mostrar(datos);
  } catch (error) {
    if (!ocupado) {
      avisoConexion = true;
      const nuevaRevision = error.status === 409;
      if (nuevaRevision !== requiereRevision) generacionRevision += 1;
      requiereRevision = nuevaRevision;
      if (!requiereRevision) { revisionReintento = null; puntoRecuperacion = null; intencionRevision = null; $("#opciones-intencion").hidden = true; $("#confirmar-intencion").checked = false; $("#opciones-recuperacion").hidden = true; $("#revision-pendiente").hidden = true; }
      if (requiereRevision) { $("#estado").textContent = "Pendiente de revisión"; $("#importe").textContent = "—"; $("#tarifas-actuales").textContent = "Pendientes de revisión."; revisionTarifas = null; generacionTarifas += 1; }
      conectado = requiereRevision;
      $("#conexion").textContent = requiereRevision ? "Revisión necesaria" : "Estado no disponible";
      $("#mensaje").textContent = requiereRevision && ultimoError ? ultimoError + " La carrera necesita revisión." : (error.status ? error.message : "No se puede confirmar el estado. Comprueba la conexión.");
      controles();
      if (requiereRevision) void mostrarPendiente();
    }
  } finally { leyendo = false; }
}
async function actuar(ruta, adicionales = {}) {
  const reintentoDurable = ruta.endsWith("/reintentar") && requiereRevision && revisionReintento !== null;
  if (ocupado || !conectado || (!actual && !reintentoDurable)) return;
  ocupado = true; ultimoError = ""; controles();
  try {
    const cuerpo = {revision: reintentoDurable ? revisionReintento : actual.revision, ...adicionales};
    if (document.body?.dataset?.durable === "true" && (ruta.endsWith("/iniciar") || ruta.endsWith("/estado"))) {
      if (!globalThis.crypto?.randomUUID) throw new Error("El navegador no permite generar un identificador seguro.");
      cuerpo.id_operacion = globalThis.crypto.randomUUID();
    }
    const datos = await solicitar(ruta, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(cuerpo)});
    conectado = true; mostrar(datos);
    await cargarHistorial();
  } catch (error) {
    conectado = false;
    ultimoError = error.message;
    $("#mensaje").textContent = error.message + " La acción podría seguir procesándose. Consulta el estado antes de volver a intentarlo.";
  } finally { ocupado = false; controles(); await consultar(); }
}
async function confirmarRecuperacion() {
  if (ocupado || !conectado || !requiereRevision || !puntoRecuperacion || !$("#confirmar-recuperacion").checked) return;
  const confirmado = {...puntoRecuperacion};
  ocupado = true; ultimoError = ""; controles();
  try {
    const datos = await solicitar("/api/carrera/confirmar-recuperacion", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({revision:confirmado.revision,
        ultimo_control:confirmado.instante, huella:confirmado.huella, confirmar:true})});
    conectado = true; mostrar(datos); await cargarHistorial();
  } catch (error) {
    conectado = false; ultimoError = error.message;
    $("#mensaje").textContent = error.message + " Comprueba el estado antes de volver a intentarlo.";
  } finally {
    ocupado = false; ultimaConsultaPendiente = 0; controles(); await consultar();
  }
}
async function confirmarIntencion() {
  if (ocupado || !conectado || !requiereRevision || !intencionRevision || !$("#confirmar-intencion").checked) return;
  const confirmada = {...intencionRevision};
  ocupado = true; ultimoError = ""; generacionRevision += 1; controles();
  try {
    const datos = await solicitar("/api/recuperacion/intencion/confirmar", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({huella:confirmada.huella, confirmar:true})});
    intencionRevision = null; $("#confirmar-intencion").checked = false;
    $("#opciones-intencion").hidden = true;
    $("#mensaje").textContent = datos.mensaje;
  } catch (error) {
    ultimoError = error.message;
    $("#mensaje").textContent = error.message + " Comprueba el estado antes de volver a intentarlo.";
  } finally {
    ocupado = false; ultimaConsultaPendiente = 0; controles(); await consultar();
  }
}
function fechaHistorial(valor) {
  const fecha = new Date(valor);
  if (Number.isNaN(fecha.getTime())) return String(valor);
  return new Intl.DateTimeFormat("es-ES", {day:"2-digit", month:"2-digit", year:"numeric",
    hour:"2-digit", minute:"2-digit", second:"2-digit"}).format(fecha);
}
function duracionHistorial(valor) {
  const numero = Number(valor);
  return Number.isFinite(numero) && numero >= 0
    ? new Intl.NumberFormat("es-ES", {maximumFractionDigits:2}).format(numero)
    : String(valor);
}
async function cargarHistorial() {
  const generacion = ++generacionHistorial;
  try {
    const datos = await solicitar("/api/historial");
    if (generacion !== generacionHistorial) return;
    const cuerpo = $("#historial"); cuerpo.replaceChildren();
    for (const carrera of datos.carreras) {
      const fila = document.createElement("tr");
      for (const valor of [fechaHistorial(carrera.inicio), duracionHistorial(carrera.duracion_segundos), String(carrera.importe).replace(".", ",") + " €"]) {
        const celda = document.createElement("td"); celda.textContent = String(valor); fila.append(celda);
      }
      cuerpo.append(fila);
    }
    $("#aviso-historial").textContent = datos.carreras.length ? "Carreras guardadas." : "Todavía no hay carreras guardadas.";
  } catch (error) { if (generacion === generacionHistorial) $("#aviso-historial").textContent = error.message; }
}
$("#iniciar").addEventListener("click", () => actuar("/api/carrera/iniciar"));
$("#mover").addEventListener("click", () => actuar("/api/carrera/estado", {estado: "en movimiento"}));
$("#parar").addEventListener("click", () => actuar("/api/carrera/estado", {estado: "parado"}));
$("#finalizar").addEventListener("click", () => actuar("/api/carrera/finalizar"));
$("#reintentar").addEventListener("click", () => actuar("/api/carrera/reintentar"));
$("#actualizar").addEventListener("click", cargarHistorial);
$("#confirmar-recuperacion").addEventListener("change", controles);
$("#finalizar-recuperada").addEventListener("click", confirmarRecuperacion);
$("#confirmar-intencion").addEventListener("change", controles);
$("#revisar-intencion").addEventListener("click", confirmarIntencion);
$("#cerrar").addEventListener("click", async () => {
  try { await solicitar("/acceso/cerrar", {method: "POST"}); window.location.assign("/acceso"); }
  catch (error) { $("#mensaje").textContent = error.message; }
});
consultar(); cargarHistorial(); cargarTarifasNuevas(); setInterval(consultar, 1000); setInterval(cargarTarifasNuevas, 30000);
