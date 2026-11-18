"use strict";

// Es una preferencia de esta pestaña; no cambia la configuración del sistema.
(() => {
  const opcion = document.querySelector("#mantener-pantalla");
  const estado = document.querySelector("#estado-pantalla");
  let activa = false;
  let bloqueo = null;
  let solicitando = false;
  let revision = 0;

  async function actualizar() {
    const actual = ++revision;
    const desea = opcion.checked && activa && document.visibilityState === "visible";
    if (!desea) {
      const anterior = bloqueo;
      bloqueo = null;
      if (anterior && !anterior.released) await anterior.release().catch(() => {});
      if (actual !== revision) return;
      estado.textContent = !opcion.checked ? "Opción desactivada." :
        (!activa ? "Se intentará mantener la pantalla encendida al iniciar una carrera." :
          "La pestaña no está visible; se intentará reactivar al volver.");
      return;
    }
    if (!("wakeLock" in navigator)) {
      estado.textContent = "Este navegador no permite mantener la pantalla encendida.";
      return;
    }
    if (bloqueo && !bloqueo.released) { estado.textContent = "Pantalla activa solicitada."; return; }
    if (solicitando) return;
    solicitando = true;
    estado.textContent = "Solicitando mantener la pantalla encendida…";
    try {
      const nuevo = await navigator.wakeLock.request("screen");
      if (actual !== revision || !opcion.checked || !activa || document.visibilityState !== "visible") {
        await nuevo.release().catch(() => {});
      } else {
        bloqueo = nuevo;
        estado.textContent = "Pantalla activa solicitada.";
        nuevo.addEventListener("release", () => {
          if (bloqueo === nuevo) {
            bloqueo = null;
            estado.textContent = "El dispositivo ha liberado la pantalla activa. Desactiva y vuelve a activar la opción para intentarlo de nuevo.";
          }
        });
      }
    } catch {
      if (actual === revision) estado.textContent = "No se ha podido mantener la pantalla encendida; comprueba el navegador y el dispositivo.";
    } finally {
      solicitando = false;
      if (actual !== revision) void actualizar();
    }
  }
  opcion.addEventListener("change", () => { void actualizar(); });
  document.addEventListener("visibilitychange", () => { void actualizar(); });
  document.addEventListener("taxiroute:carrera", (evento) => {
    const siguiente = evento.detail?.activa === true;
    if (siguiente !== activa) { activa = siguiente; void actualizar(); }
  });
})();
