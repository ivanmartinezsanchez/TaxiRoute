"use strict";
const contenedor = document.querySelector("#cuentas");
const mensaje = document.querySelector("#mensaje");
const descripciones = {
  desactivar: ["Desactivar", "Impide el acceso y revoca las sesiones del conductor."],
  reactivar: ["Reactivar", "Habilita la cuenta, pero conserva cualquier bloqueo por intentos."],
  desbloquear: ["Desbloquear", "Borra el bloqueo, los fallos y la espera, pero no habilita una cuenta desactivada."],
  revocar: ["Revocar sesiones", "Cierra las sesiones del conductor sin desactivar su cuenta."]
};
let ocupado = false;
let actualizarBotonesHistorial = () => {};
let actualizarConfirmacionesRevision = () => {};
async function solicitar(ruta, opciones = {}) {
  const respuesta = await fetch(ruta, {...opciones, cache:"no-store", signal:AbortSignal.timeout(5000)});
  if (respuesta.status === 401) { window.location.assign("/acceso"); throw new Error("Sesión no disponible."); }
  const datos = await respuesta.json().catch(() => null);
  if (!respuesta.ok || !datos) throw new Error(datos?.error || "No se ha podido completar la operación.");
  return datos;
}
async function cargar() {
  try {
    const datos = await solicitar("/api/administracion/conductores");
    contenedor.replaceChildren();
    const actualizadoresRevision = [];
    actualizarConfirmacionesRevision = () => actualizadoresRevision.forEach((actualizar) => actualizar());
    for (const cuenta of datos.conductores) {
      const tarjeta = document.createElement("article"); tarjeta.className = "cuenta";
      const titulo = document.createElement("h2"); titulo.textContent = cuenta.nombre_visible + " (" + cuenta.usuario + ")";
      const estado = document.createElement("p"); estado.textContent = (cuenta.habilitado ? "Cuenta habilitada" : "Cuenta desactivada") + (cuenta.bloqueado ? " · Bloqueada por intentos" : " · Sin bloqueo por intentos");
      const acciones = document.createElement("div"); acciones.className = "acciones";
      for (const [accion, descripcion] of Object.entries(descripciones)) {
        const bloque = document.createElement("div"); bloque.className = "accion";
        const boton = document.createElement("button"); boton.type = "button"; boton.textContent = descripcion[0];
        const ayuda = document.createElement("p"); ayuda.textContent = descripcion[1];
        boton.addEventListener("click", async () => {
          if (ocupado) return;
          ocupado = true; document.querySelectorAll("button").forEach((x) => { x.disabled = true; });
          mensaje.textContent = "Aplicando la acción…";
          try {
            const resultado = await solicitar("/api/administracion/conductores/" + encodeURIComponent(cuenta.id) + "/accion",
              {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({accion})});
            const actualizado = await cargar();
            mensaje.textContent = actualizado ? resultado.mensaje : "Acción realizada, pero no se pudo actualizar la lista. Inténtalo de nuevo.";
          } catch (error) { mensaje.textContent = error.message; }
          finally { ocupado = false; document.querySelectorAll("button").forEach((x) => { x.disabled = false; }); actualizarBotonesHistorial(); actualizarConfirmacionesRevision(); }
        });
        bloque.append(boton, ayuda); acciones.append(bloque);
      }
      const formulario = document.createElement("form");
      const etiqueta = document.createElement("label");
      etiqueta.textContent = "Nueva contraseña temporal para " + cuenta.nombre_visible;
      const clave = document.createElement("input"); clave.type = "password";
      clave.autocomplete = "new-password"; clave.required = true; clave.minLength = 12; clave.maxLength = 128;
      etiqueta.append(clave);
      const ayudaClave = document.createElement("p");
      ayudaClave.textContent = "Revoca sus sesiones. Deberá cambiar esta contraseña al entrar. El bloqueo y la desactivación se mantienen.";
      const botonClave = document.createElement("button"); botonClave.type = "submit"; botonClave.textContent = "Restablecer contraseña";
      formulario.append(etiqueta, ayudaClave, botonClave);
      formulario.addEventListener("submit", async (evento) => {
        evento.preventDefault();
        if (ocupado) return;
        const datos = {contrasena: clave.value}; clave.value = ""; ocupado = true; botonClave.disabled = true;
        try {
          const resultado = await solicitar("/api/administracion/conductores/" + encodeURIComponent(cuenta.id) + "/restablecer-contrasena", {
            method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(datos)});
          mensaje.textContent = resultado.mensaje;
        } catch (error) { mensaje.textContent = error.message; }
        finally { datos.contrasena = ""; ocupado = false; botonClave.disabled = false; }
      });
      tarjeta.append(titulo, estado, acciones, formulario);
      if (document.body?.dataset?.durable === "true" && document.body?.dataset?.persistente === "true") {
        const revision = document.createElement("section"); revision.className = "revision-cuenta";
        const subtitulo = document.createElement("h3"); subtitulo.textContent = "Acción pendiente de revisión";
        const consultarRevision = document.createElement("button"); consultarRevision.type = "button";
        consultarRevision.textContent = "Consultar acción pendiente";
        const detalle = document.createElement("p"); detalle.setAttribute("role", "status"); detalle.setAttribute("aria-live", "polite");
        const opciones = document.createElement("div"); opciones.hidden = true;
        const advertencia = document.createElement("p");
        const etiquetaRevision = document.createElement("label");
        const aceptar = document.createElement("input"); aceptar.type = "checkbox";
        const textoAceptar = document.createElement("span"); etiquetaRevision.append(aceptar, textoAceptar);
        const confirmarRevision = document.createElement("button"); confirmarRevision.type = "button";
        confirmarRevision.textContent = "Confirmar revisión"; confirmarRevision.disabled = true;
        let diagnostico = null;
        const activar = () => { confirmarRevision.disabled = ocupado || !diagnostico || !aceptar.checked; };
        actualizadoresRevision.push(activar);
        aceptar.addEventListener("change", activar);
        const ruta = "/api/recuperacion/administracion/" + encodeURIComponent(cuenta.id) + "/intencion";
        consultarRevision.addEventListener("click", async () => {
          if (ocupado) return;
          ocupado = true; consultarRevision.disabled = true; diagnostico = null; aceptar.checked = false; opciones.hidden = true; activar();
          detalle.textContent = "Consultando la acción pendiente…";
          try {
            const datos = await solicitar(ruta);
            const intencion = datos.intencion;
            if (!intencion) { detalle.textContent = "No hay una acción pendiente de revisión para esta cuenta."; return; }
            if (!["iniciar", "mover", "parar"].includes(intencion.accion) || typeof intencion.huella !== "string" || !/^[0-9a-f]{64}$/.test(intencion.huella))
              throw new Error("No se puede verificar el diagnóstico. Vuelve a consultarlo.");
            const nombres = {iniciar:"iniciar la carrera", mover:"poner el vehículo en movimiento", parar:"parar el vehículo"};
            const punto = datos.pendiente;
            if (intencion.accion === "iniciar" && punto != null) throw new Error("El diagnóstico necesita revisión adicional.");
            if (intencion.accion !== "iniciar" && (!punto || typeof punto.ultimo_control !== "string" || typeof punto.estado_vehiculo !== "string"))
              throw new Error("No hay datos fiables suficientes para revisar la carrera.");
            detalle.textContent = "No se pudo confirmar si se completó la acción de " + nombres[intencion.accion] + ".";
            if (intencion.accion === "iniciar") {
              advertencia.textContent = "No existe un punto ni un importe fiable. Confirmar no crea una carrera ni calcula el intervalo sin datos.";
              textoAceptar.textContent = " He revisado lo ocurrido con el conductor y entiendo que no existe un punto ni un importe fiable.";
            } else {
              const importe = typeof punto.importe_recuperado === "string" ? " Importe hasta ese punto: " + punto.importe_recuperado.replace(".", ",") + " €." : "";
              advertencia.textContent = "Último punto: " + punto.ultimo_control + "; estado registrado: " + punto.estado_vehiculo + "." + importe + " El intervalo posterior es desconocido y no se cobrará automáticamente. La carrera seguirá pendiente de cierre.";
              textoAceptar.textContent = " He revisado los datos y entiendo que no se conoce el estado ni el tiempo posterior al último punto.";
            }
            diagnostico = {accion:intencion.accion, huella:intencion.huella}; opciones.hidden = false;
          } catch (error) { detalle.textContent = error.message; }
          finally { ocupado = false; consultarRevision.disabled = false; activar(); }
        });
        confirmarRevision.addEventListener("click", async () => {
          if (ocupado || !diagnostico || !aceptar.checked) return;
          const revisada = {...diagnostico}; ocupado = true; consultarRevision.disabled = true; activar();
          detalle.textContent = "Registrando la revisión…";
          try {
            await solicitar(ruta + "/resolver", {method:"POST", headers:{"Content-Type":"application/json"},
              body:JSON.stringify({huella:revisada.huella, confirmar:true})});
            diagnostico = null; aceptar.checked = false; opciones.hidden = true;
            detalle.textContent = revisada.accion === "iniciar"
              ? "Revisión registrada. No se ha creado una carrera para el intervalo sin datos."
              : "Revisión registrada. La carrera sigue pendiente de cerrar en el último punto fiable.";
          } catch (error) { detalle.textContent = error.message + " Comprueba el estado antes de volver a intentarlo."; }
          finally { ocupado = false; consultarRevision.disabled = false; activar(); }
        });
        opciones.append(advertencia, etiquetaRevision, confirmarRevision);
        revision.append(subtitulo, consultarRevision, detalle, opciones); tarjeta.append(revision);
      }
      contenedor.append(tarjeta);
    }
    mensaje.textContent = datos.conductores.length ? "Cuentas actualizadas." : "No hay conductores registrados.";
    return true;
  } catch (error) { mensaje.textContent = error.message; return false; }
}
document.querySelector("#form-alta").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  if (ocupado) return;
  const clave = document.querySelector("#alta-clave");
  const confirmar = document.querySelector("#alta-confirmar");
  if (clave.value !== confirmar.value) {
    mensaje.textContent = "Las contraseñas no coinciden.";
    confirmar.focus();
    return;
  }
  const datos = {usuario:document.querySelector("#alta-usuario").value,
                 nombre_visible:document.querySelector("#alta-nombre").value,
                 contrasena:clave.value};
  clave.value = ""; confirmar.value = "";
  ocupado = true;
  const boton = evento.currentTarget.querySelector("button");
  boton.disabled = true;
  try {
    const resultado = await solicitar("/api/administracion/conductores", {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(datos)});
    datos.contrasena = "";
    evento.target.reset();
    const actualizado = await cargar();
    mensaje.textContent = actualizado ? "Conductor creado. Debe cambiar su contraseña antes de acceder."
      : "Conductor creado, pero no se pudo actualizar la lista.";
  } catch (error) { mensaje.textContent = error.message; }
  finally { datos.contrasena = ""; ocupado = false; boton.disabled = false; }
});
async function cargarTarifas() {
  try {
    const datos = await solicitar("/api/tarifas");
    document.querySelector("#tarifa-parado").value = datos.tarifas.parado;
    document.querySelector("#tarifa-movimiento").value = datos.tarifas["en movimiento"];
  } catch (error) { mensaje.textContent = error.message; }
}
document.querySelector("#form-tarifas").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  if (ocupado) return;
  ocupado = true;
  const boton = evento.currentTarget.querySelector("button");
  boton.disabled = true;
  try {
    const resultado = await solicitar("/api/tarifas", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({parado:document.querySelector("#tarifa-parado").value,
                           en_movimiento:document.querySelector("#tarifa-movimiento").value})});
    mensaje.textContent = resultado.mensaje;
  } catch (error) { mensaje.textContent = error.message; }
  finally { ocupado = false; boton.disabled = false; }
});
cargarTarifas();document.querySelector("#actualizar").addEventListener("click", cargar);
cargar();
const selectorHistorial = document.querySelector("#periodo-historial");
if (selectorHistorial) {
  let generacionHistorial = 0;
  let pagina = 1;
  let hayAnterior = false;
  let hayMas = false;
  let cargandoHistorial = false;
  const anterior = document.querySelector("#historial-anterior");
  const siguiente = document.querySelector("#historial-siguiente");
  actualizarBotonesHistorial = () => {
    anterior.disabled = cargandoHistorial || !hayAnterior;
    siguiente.disabled = cargandoHistorial || !hayMas;
  };
  const estadoHistorial = document.querySelector("#estado-historial-flota");
  const filasHistorial = document.querySelector("#filas-historial-flota");
  function fechaLocal(valor) {
    const fecha = new Date(valor);
    return Number.isNaN(fecha.getTime()) ? "Fecha no válida" : new Intl.DateTimeFormat("es-ES", {
      dateStyle:"short", timeStyle:"medium"}).format(fecha);
  }
  async function cargarHistorialFlota() {
    const generacion = ++generacionHistorial;
    const periodo = selectorHistorial.value;
    cargandoHistorial = true; actualizarBotonesHistorial();
    estadoHistorial.textContent = "Cargando historial…";
    try {
      const datos = await solicitar("/api/administracion/historial?periodo=" + encodeURIComponent(periodo) + "&pagina=" + pagina);
      if (generacion !== generacionHistorial) return;
      filasHistorial.replaceChildren();
      for (const carrera of datos.carreras) {
        const fila = document.createElement("tr");
        for (const valor of [carrera.conductor, fechaLocal(carrera.inicio), fechaLocal(carrera.fin),
                             carrera.duracion_segundos, String(carrera.importe).replace(".", ",") + " €"]) {
          const celda = document.createElement("td"); celda.textContent = String(valor); fila.append(celda);
        }
        filasHistorial.append(fila);
      }
      hayAnterior = datos.hay_anterior; hayMas = datos.hay_mas;
      document.querySelector("#pagina-historial").textContent = "Página " + datos.pagina;
      estadoHistorial.textContent = datos.carreras.length
        ? "Carreras en esta página: " + datos.carreras.length + "."
        : "No hay carreras en esta página.";
    } catch (error) {
      if (generacion === generacionHistorial) {
        filasHistorial.replaceChildren();
        estadoHistorial.textContent = error.message;
      }
    } finally {
      if (generacion === generacionHistorial) { cargandoHistorial = false; actualizarBotonesHistorial(); }
    }
  }
  selectorHistorial.addEventListener("change", () => { pagina = 1; cargarHistorialFlota(); });
  anterior.addEventListener("click", () => { if (hayAnterior && !cargandoHistorial) { pagina -= 1; cargarHistorialFlota(); } });
  siguiente.addEventListener("click", () => { if (hayMas && !cargandoHistorial) { pagina += 1; cargarHistorialFlota(); } });
  document.querySelector("#actualizar-historial").addEventListener("click", cargarHistorialFlota);
  cargarHistorialFlota();
}
