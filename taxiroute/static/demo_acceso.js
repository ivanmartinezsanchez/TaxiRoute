"use strict";

const credenciales = document.querySelector("#form-credenciales");
const formularioPin = document.querySelector("#form-pin");
const cambio = document.querySelector("#form-cambio");
const mensaje = document.querySelector("#mensaje");

function mostrar(formulario, texto) {
  for (const item of [credenciales, formularioPin, cambio]) item.hidden = item !== formulario;
  mensaje.textContent = texto;
  formulario.querySelector("input").focus();
}

async function enviar(formulario, ruta, datos) {
  const boton = formulario.querySelector("button");
  boton.disabled = true;
  mensaje.textContent = "Comprobando…";
  try {
    const respuesta = await fetch(ruta, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datos),
    });
    const resultado = await respuesta.json();
    if (!respuesta.ok) {
      mensaje.textContent = resultado.error || "No se ha podido completar la operación.";
      return null;
    }
    return resultado;
  } catch {
    mensaje.textContent = "No se ha podido conectar. Comprueba la aplicación y vuelve a intentarlo.";
    return null;
  } finally {
    boton.disabled = false;
  }
}

credenciales.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const clave = document.querySelector("#contrasena");
  const datos = { usuario: document.querySelector("#usuario").value, contrasena: clave.value };
  clave.value = "";
  const resultado = await enviar(credenciales, "/acceso/administrador", datos);
  datos.contrasena = "";
  if (resultado && resultado.paso === "pin_pendiente")
    mostrar(formularioPin, resultado.codigo_demostracion
      ? "Código ficticio de esta demostración: " + resultado.codigo_demostracion
      : "Introduce el código enviado para esta prueba.");
});

formularioPin.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.querySelector("#codigo");
  const resultado = await enviar(formularioPin, "/acceso/administrador/pin", { codigo: campo.value });
  campo.value = "";
  if (!resultado) return;
  if (resultado.paso === "cambio_pendiente")
    mostrar(cambio, "Debes cambiar la contraseña temporal.");
  else if (resultado.paso === "acceso_completado")
    window.location.assign("/panel");
});

cambio.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.querySelector("#nueva");
  const datos = { contrasena_nueva: campo.value };
  campo.value = "";
  const resultado = await enviar(cambio, "/acceso/administrador/cambio", datos);
  datos.contrasena_nueva = "";
  if (resultado && resultado.paso === "volver_a_iniciar")
    mostrar(credenciales, "Contraseña cambiada. Vuelve a iniciar sesión.");
});
