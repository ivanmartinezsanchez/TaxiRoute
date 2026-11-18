"use strict";

const acceso = document.querySelector("#form-acceso");
const pin = document.querySelector("#form-pin");
const cambio = document.querySelector("#form-cambio");
const cambioConductor = document.querySelector("#form-cambio-conductor");
const mensaje = document.querySelector("#mensaje");
const titulo = document.querySelector("#titulo-formulario");
const formularios = [acceso, pin, cambio, cambioConductor];

function mostrar(formulario, encabezado, texto) {
  for (const elemento of formularios) elemento.hidden = elemento !== formulario;
  titulo.textContent = encabezado;
  mensaje.textContent = texto;
  formulario.querySelector("input, select").focus();
}

function ocupado(formulario, valor) {
  formulario.setAttribute("aria-busy", String(valor));
  formulario.querySelectorAll("button").forEach((boton) => { boton.disabled = valor; });
}

async function enviar(formulario, ruta, datos) {
  ocupado(formulario, true);
  mensaje.textContent = "Comprobando…";
  try {
    const respuesta = await fetch(ruta, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datos),
    });
    const resultado = await respuesta.json().catch(() => null);
    if (!respuesta.ok || !resultado || typeof resultado !== "object") {
      mensaje.textContent = resultado && typeof resultado.error === "string"
        ? resultado.error : "No se ha podido completar la operación. Comprueba los datos e inténtalo de nuevo.";
      return null;
    }
    return resultado;
  } catch {
    mensaje.textContent = "No se ha podido conectar con la aplicación. Comprueba que esté abierta y vuelve a intentarlo.";
    return null;
  } finally {
    ocupado(formulario, false);
  }
}

acceso.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const tipo = document.querySelector("#tipo").value;
  const clave = document.querySelector("#contrasena");
  const datos = { usuario: document.querySelector("#usuario").value, contrasena: clave.value };
  clave.value = "";
  const resultado = await enviar(acceso, tipo === "administrador" ? "/acceso/administrador" : "/acceso/conductor", datos);
  datos.contrasena = "";
  if (!resultado) return;
  if (tipo === "conductor" && typeof resultado.mensaje === "string") {
    window.location.assign("/taximetro");
  } else if (tipo === "administrador" && resultado.paso === "pin_pendiente") {
    const instruccion = document.body.dataset.pinDemoTerminal === "true"
      ? "Introduce el código ficticio que aparece en la terminal."
      : "Introduce el código que has recibido por correo.";
    mostrar(pin, "Verificar acceso", instruccion);
  } else {
    mensaje.textContent = "La aplicación no ha podido completar el acceso. Vuelve a intentarlo.";
  }
});

document.querySelector("#ir-cambio-conductor").addEventListener("click", () => {
  document.querySelector("#usuario-cambio").value = document.querySelector("#usuario").value;
  mostrar(cambioConductor, "Cambiar contraseña", "Introduce tu contraseña actual y elige una nueva.");
});

cambioConductor.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const actual = document.querySelector("#actual-conductor");
  const nueva = document.querySelector("#nueva-conductor");
  const confirmacion = document.querySelector("#confirmar-conductor");
  if (nueva.value !== confirmacion.value) {
    mensaje.textContent = "Las contraseñas no coinciden. Revísalas y vuelve a intentarlo.";
    confirmacion.focus();
    return;
  }
  const datos = {usuario: document.querySelector("#usuario-cambio").value,
                 contrasena_actual: actual.value, contrasena_nueva: nueva.value};
  actual.value = ""; nueva.value = ""; confirmacion.value = "";
  const resultado = await enviar(cambioConductor, "/acceso/conductor/cambio", datos);
  datos.contrasena_actual = ""; datos.contrasena_nueva = "";
  if (resultado && resultado.paso === "volver_a_iniciar") {
    mostrar(acceso, "Iniciar sesión", "Contraseña cambiada. Vuelve a iniciar sesión.");
  }
});
pin.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.querySelector("#codigo");
  const datos = { codigo: campo.value };
  campo.value = "";
  const resultado = await enviar(pin, "/acceso/administrador/pin", datos);
  datos.codigo = "";
  if (!resultado) return;
  if (resultado.paso === "cambio_pendiente") {
    mostrar(cambio, "Cambiar contraseña", "Crea una contraseña nueva para terminar la configuración.");
  } else if (resultado.paso === "acceso_completado") {
    window.location.assign("/taximetro");
  } else {
    mensaje.textContent = "La aplicación no ha podido completar la verificación.";
  }
});

cambio.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const clave = document.querySelector("#nueva");
  const confirmacion = document.querySelector("#confirmacion");
  if (clave.value !== confirmacion.value) {
    mensaje.textContent = "Las contraseñas no coinciden. Revísalas y vuelve a intentarlo.";
    confirmacion.focus();
    return;
  }
  const datos = { contrasena_nueva: clave.value };
  clave.value = "";
  confirmacion.value = "";
  const resultado = await enviar(cambio, "/acceso/administrador/cambio", datos);
  datos.contrasena_nueva = "";
  if (resultado && resultado.paso === "volver_a_iniciar") {
    mostrar(acceso, "Iniciar sesión", "Contraseña cambiada. Vuelve a iniciar sesión.");
  } else if (resultado) {
    mensaje.textContent = "La aplicación no ha podido completar el cambio de contraseña.";
  }
});

document.querySelectorAll(".volver").forEach((boton) => {
  boton.addEventListener("click", () => {
    pin.reset();
    cambio.reset();
    cambioConductor.reset();
    mostrar(acceso, "Iniciar sesión", "Has vuelto al inicio de sesión.");
  });
});

document.querySelectorAll("[data-mostrar]").forEach((boton) => {
  boton.addEventListener("click", () => {
    const campo = document.getElementById(boton.dataset.mostrar);
    const visible = campo.type === "password";
    campo.type = visible ? "text" : "password";
    boton.textContent = visible ? "Ocultar" : "Mostrar";
    boton.setAttribute("aria-pressed", String(visible));
  });
});
