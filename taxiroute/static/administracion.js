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
          finally { ocupado = false; document.querySelectorAll("button").forEach((x) => { x.disabled = false; }); }
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
      tarjeta.append(titulo, estado, acciones, formulario); contenedor.append(tarjeta);
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