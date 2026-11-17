# Manual de uso de TaxiRoute local

Esta guía corresponde a la instalación de TaxiRoute con cuenta propia en Windows y Docker. Permite entrar como conductor o administrador, gestionar carreras y consultar el historial desde el ordenador donde está instalada.

La instalación sigue en preparación: utiliza datos de prueba y no está validada para cobros reales. El acceso de esta instalación está limitado al propio ordenador. La prueba desde el móvil se hizo con una instalación separada; no significa que esta instalación esté disponible en el teléfono.

## 1. Arrancar la aplicación

1. Abre Docker Desktop y espera a que termine de iniciarse.
2. Abre una terminal en la carpeta del proyecto. En Windows puedes usar la terminal de VS Code. Si todavía no existe `.venv`, necesitas Python 3.13 accesible mediante el lanzador `py` y conexión a internet: el arrancador crea el entorno e instala las dependencias. Si el entorno ya existe, lo utiliza sin reinstalarlo.
3. Ejecuta el comando correspondiente a tu terminal:

   **Git Bash:**

   ```bash
   ./iniciar-taxiroute-proxy-experimental.cmd --conectar-gmail
   ```

   **PowerShell:**

   ```powershell
   .\iniciar-taxiroute-proxy-experimental.cmd --conectar-gmail
   ```

   **Símbolo del sistema (cmd):**

   ```cmd
   iniciar-taxiroute-proxy-experimental.cmd --conectar-gmail
   ```

   La opción solicita cargar el perfil de Gmail previamente autorizado, necesario para enviar el código de acceso del administrador. No envía un correo por el mero hecho de arrancar.

4. Abre [TaxiRoute en este ordenador](https://127.0.0.1:18771/). El certificado local es autofirmado, por lo que el navegador puede mostrar una advertencia. La confianza HTTPS definitiva sigue pendiente.

La primera vez se solicita crear el administrador y guardar cinco códigos de recuperación. Guárdalos en privado. Si te equivocas al escribir `GUARDADOS`, el asistente vuelve a pedir la confirmación; pulsa Intro para cancelar. Si la preparación se interrumpe y queda una carpeta parcial, consérvala para revisión en lugar de borrarla o repetir la creación a ciegas.

Si la preparación del entorno se interrumpe, conserva la carpeta `.venv` y revisa el error antes de volver a intentarlo. Si el lanzador indica que el código del proyecto y el contenedor son distintos, la instalación sigue usando la imagen anterior. Descargar cambios del repositorio no actualiza el contenedor automáticamente. No lo borres para forzar la actualización: el procedimiento seguro de sustitución sigue pendiente.

## 2. Entrar

En la pantalla de acceso, selecciona **Conductor** o **Administrador** y escribe tu usuario y contraseña.

- **Conductor:** si la contraseña es temporal, la aplicación solicitará cambiarla antes de entrar.
- **Administrador:** además de la contraseña, escribe en la página el código de siete cifras enviado al correo configurado. El código caduca a los cinco minutos. Si se solicita, cambia la contraseña temporal antes de continuar.

No compartas contraseñas, códigos de correo ni códigos de recuperación. Si el correo no llega, revisa la carpeta de spam y comprueba que el perfil de Gmail esté autorizado y que el lanzador se haya iniciado con `--conectar-gmail`. Un archivo temporal de autorización existente no garantiza por sí solo que la autorización siga vigente.

La sesión caduca tras una hora sin interacción o, como máximo, ocho horas después de iniciarse. Las consultas automáticas de pantalla no prolongan ese plazo. Si caduca, vuelve a entrar; cerrar o perder la sesión no finaliza automáticamente la carrera.

## 3. Hacer una carrera

1. En **Tu taxímetro**, pulsa **Iniciar carrera**. Comienza en estado parado.
2. Pulsa **En movimiento** cuando corresponda y **Parado** cuando el vehículo vuelva a detenerse. El estado se indica manualmente; la aplicación no mide la velocidad.
3. Consulta el importe y el estado en pantalla. Se actualizan mientras el servicio está disponible.
4. Pulsa **Finalizar carrera** y comprueba el resultado y su aparición en **Mis carreras**. Puedes iniciar otra sin cerrar la aplicación.

Las tarifas que se muestran para la carrera actual son las fijadas al iniciarla. Si el administrador las cambia durante la carrera, las nuevas se aplican a las carreras que se inicien después.

La opción **Mantener la pantalla encendida durante una carrera** depende del dispositivo y del navegador. No impide el bloqueo manual ni garantiza que el equipo no se suspenda.

## 4. Consultar el historial

En **Mis carreras**, pulsa **Actualizar** si necesitas volver a consultar los datos. El selector permite ver todas las carreras o las finalizadas hoy. El filtro de la pantalla del conductor utiliza la zona horaria de ese dispositivo.

El administrador dispone además de **Historial de la flota**, con filtros y paginación. Su filtro de hoy utiliza la zona horaria del servidor. Los historiales anteriores sin propietario no se asignan automáticamente a un conductor.

## 5. Administrar tarifas y conductores

Entra como administrador y selecciona **Administración**. Desde allí puedes volver al taxímetro y actuar también como conductor.

En **Tarifas para nuevas carreras**, introduce los importes por segundo y pulsa **Guardar tarifas**. Usa un punto para los decimales. El cambio no modifica las tarifas de una carrera que ya está en marcha.

En **Añadir conductor**, introduce usuario, nombre visible y contraseña temporal; repítela y pulsa **Crear conductor**. La contraseña debe tener de 12 a 128 caracteres, al menos una mayúscula, tres minúsculas, tres números y un símbolo. Comunícala por un medio seguro. El conductor deberá cambiarla al primer acceso.

Para cambiar la contraseña de un conductor, escribe una nueva contraseña temporal en su ficha y pulsa **Restablecer contraseña**. Se revocan sus sesiones y tendrá que cambiarla al entrar; un bloqueo o una desactivación existentes se conservan. Comunica la contraseña por un medio seguro.

En **Cuentas de conductores**, las acciones tienen efectos distintos:

- **Desactivar:** impide el acceso y revoca las sesiones. Antes hay que finalizar o revisar cualquier carrera pendiente.
- **Reactivar:** habilita la cuenta, pero conserva un posible bloqueo por intentos.
- **Desbloquear:** borra el bloqueo, los fallos y la espera; no habilita una cuenta que esté desactivada.
- **Revocar sesiones:** cierra las sesiones sin desactivar la cuenta ni finalizar su carrera.

## 6. Si se interrumpe una carrera

Si la aplicación muestra **Carrera u operación pendiente de revisión**, lee los datos del último punto guardado. Cuando se ofrezca la opción de finalizar en ese punto, revisa el importe y confirma solo si estás de acuerdo. El tiempo posterior al último punto es desconocido y no se cobra automáticamente.

Si aparece **Reintentar guardado**, consulta el estado antes de volver a intentarlo. Una copia CSV de la carrera pendiente, cuando esté disponible, no confirma que se haya guardado en el historial. Si faltan datos fiables o la aplicación pide intervención, no inventes el intervalo: revisa el caso con administración.

Si se pierde la respuesta al finalizar, comprueba **Mis carreras** y el estado antes de repetir. El cierre todavía no dispone de un mecanismo completo de reintento idempotente: un error de conexión o de sesión no demuestra por sí solo que no se haya guardado.

## 7. Cerrar

Finaliza las carreras y comprueba que estén guardadas. Utiliza **Cerrar sesión** cuando termines. Cerrar sesión no finaliza automáticamente una carrera activa.

Antes de detener Docker, comprueba que no quedan carreras u operaciones pendientes. La recuperación de copias y la actualización de la imagen de una instalación existente siguen en desarrollo: conserva los datos y no elimines ni recrees los contenedores para resolver un error sin revisar la situación.
