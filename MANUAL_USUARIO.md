# Manual de usuario: demostración local de TaxiRoute

Este manual explica cómo utilizar la **demostración SQLite con Docker** del proyecto. Su objetivo es enseñar el recorrido de una carrera y la administración de una flota con datos ficticios.

La demostración no es una instalación para uso real: utiliza cuentas de prueba conocidas, muestra el código del administrador en los registros y no envía correos. El servicio se publica únicamente en este ordenador, aunque otros contenedores de su misma red podrían alcanzarlo. No introduzcas contraseñas personales ni datos reales de clientes o conductores.

## Antes de comenzar

Necesitas Docker Desktop instalado y en funcionamiento, conexión para construir la imagen la primera vez y una copia del repositorio de TaxiRoute. Abre Git Bash o una terminal y entra en la carpeta del proyecto: es la carpeta que contiene `compose.sqlite-demo.yaml`.

La demostración está en la rama `feature/fase-4-produccion`. Si acabas de clonar el repositorio, cambia a ella con `git switch feature/fase-4-produccion` desde la carpeta del proyecto.

Las cuentas y contraseñas **ficticias** de la demostración están en el apartado [Demostración SQLite con Docker del README](README.md#demostración-sqlite-con-docker). Si alguien ha cambiado o desactivado una cuenta de demostración, el volumen conserva ese cambio y las credenciales iniciales pueden dejar de servir.

## 1. Iniciar la demostración

Con Docker Desktop abierto, ejecuta desde la carpeta del proyecto:

```bash
docker compose -f compose.sqlite-demo.yaml up --build -d
```

El comando construye la imagen si hace falta y arranca el servicio en segundo plano. Para comprobar su estado:

```bash
docker compose -f compose.sqlite-demo.yaml ps
```

Abre [TaxiRoute en este ordenador](https://127.0.0.1:18769/). El certificado de la demo es autofirmado y cambia al reiniciar, por lo que el navegador puede mostrar una advertencia. Si decides continuar, debes gestionarla tú en el navegador. Esta excepción corresponde solo a esta demo local.

## 2. Acceder como conductor

1. En «Tipo de cuenta», selecciona **Conductor**.
2. Introduce el usuario ficticio `AnaDemo` y su contraseña de prueba, indicada en el README.
3. Pulsa **Continuar**. Aparecerá el taxímetro y la sección **Mis carreras**.

Si ya has iniciado sesión con otra cuenta, utiliza **Cerrar sesión** antes de cambiar de usuario. Cerrar la sesión no finaliza una carrera activa.

## 3. Realizar una carrera

1. Pulsa **Iniciar carrera**. La carrera comienza en estado **parado** y empieza a acumular importe.
2. Usa **En movimiento** cuando el vehículo esté en marcha y **Parado** cuando se detenga. El cambio es manual; la aplicación no lee la velocidad del vehículo.
3. Consulta el estado y el importe, que se actualizan mientras el servicio está disponible.
4. Pulsa **Finalizar carrera** para detener el cálculo y guardar el resultado. Comprueba en pantalla que el guardado se ha completado.
5. Puedes iniciar otra carrera sin cerrar la aplicación.

El importe final se muestra en euros con dos decimales. En **Tarifas** puedes consultar las que usa la carrera actual y las previstas para carreras nuevas. Si el administrador cambia las tarifas durante una carrera, esta conserva las que tenía al empezar; el cambio se aplica a las siguientes.

La casilla **Mantener la pantalla encendida durante una carrera** depende del navegador y del dispositivo: no impide un bloqueo manual ni garantiza que el equipo no se suspenda. No se ha comprobado en una tablet física para esta entrega.

## 4. Consultar el historial

En **Mis carreras**, selecciona **Todas las carreras** o **Finalizadas hoy** y pulsa **Actualizar** si lo necesitas. La lista muestra el inicio, la duración y el importe. En «Mis carreras», «hoy» se calcula con la fecha de finalización y la zona horaria del navegador. En el historial de la flota del administrador se usa la zona horaria del servidor, que en Docker puede ser distinta; cerca de medianoche los resultados pueden diferir.

El historial de esta demo se guarda en un volumen separado de Docker y permanece tras detener y volver a iniciar el contenedor. La eliminación del volumen borraría esos datos de prueba; los comandos de parada de este manual no lo eliminan.

## 5. Acceder como administrador

1. Cierra la sesión anterior si está abierta. En la pantalla de acceso, selecciona **Administrador**.
2. Introduce el usuario ficticio `AdminDemo` y su contraseña de prueba del README. Pulsa **Continuar**.
3. Cuando se solicite el código, vuelve a la terminal y consulta los registros:

   ```bash
   docker compose -f compose.sqlite-demo.yaml logs --tail 30
   ```

4. Busca la línea **PIN ficticio de administrador** más reciente e introduce ese código en la pantalla de TaxiRoute. Después pulsa **Verificar código**.
5. Desde el taxímetro, abre **Administración**. El administrador también puede volver al taxímetro y utilizarlo como conductor.

En esta demostración el PIN aparece en los registros y **no se envía a Gmail**. Si el código caduca, solicita uno nuevo y vuelve a consultar el registro más reciente. No compartas códigos ni registros con otras personas.

## 6. Utilizar el panel de administración

- En **Tarifas para nuevas carreras**, escribe los importes por segundo utilizando un punto decimal y pulsa **Guardar tarifas**. Las carreras en curso mantienen sus tarifas iniciales.
- En **Historial de la flota**, elige las carreras finalizadas hoy o todas y pulsa **Actualizar historial**. Usa **Anterior** y **Siguiente** para navegar entre páginas cuando estén disponibles.
- En **Añadir conductor**, puedes crear una cuenta ficticia con una contraseña temporal que cumpla las reglas indicadas en pantalla. La persona tendrá que cambiarla en su primer acceso.
- En **Cuentas de conductores**, consulta cada estado antes de desactivar, reactivar, desbloquear o revocar sesiones. La pantalla explica el efecto de cada acción. Si una carrera o acción está pendiente, puede ser necesario finalizarla o revisarla antes de desactivar la cuenta.

Usa **Volver al taxímetro** para salir del panel. Las cuentas y cambios hechos en la demo son ficticios y permanecen en su volumen.

## 7. Detener y volver a abrir

Antes de detener el servicio, finaliza las carreras de prueba que estén activas y comprueba que se hayan guardado. Para detener y retirar el contenedor:

```bash
docker compose -f compose.sqlite-demo.yaml down
```

Este comando conserva el volumen de demostración. Para volver a abrir TaxiRoute, ejecuta otra vez el comando de inicio de la sección 1 y entra en la misma dirección. El certificado cambia al reiniciar y el navegador puede volver a mostrar una advertencia.

## Si algo no funciona

- **La página no abre:** comprueba que Docker Desktop esté en marcha, que ejecutas los comandos en la carpeta del proyecto y revisa `docker compose -f compose.sqlite-demo.yaml ps`.
- **El puerto está ocupado:** consulta los registros de la demo y revisa qué aplicación utiliza el puerto `18769` antes de volver a intentarlo.
- **No puedo entrar:** verifica el tipo de cuenta y utiliza las credenciales ficticias correspondientes. Si la sesión anterior está abierta, ciérrala. Las modificaciones de cuentas de prueba persisten entre reinicios.
- **No encuentro el PIN:** solicita uno nuevo y vuelve a ejecutar el comando de registros del apartado 5. Esta demo no envía correo.
- **Una carrera necesita revisión tras una interrupción:** sigue el aviso de la pantalla y revisa el último punto guardado. El tiempo posterior no se cobra automáticamente; la recuperación es experimental y no cubre todas las interrupciones.
- **Aparece un error de guardado:** sigue la indicación de la aplicación y, si se ofrece, usa **Reintentar guardado**. Una copia descargada no confirma que se haya guardado en el historial.

Si el problema continúa, conserva los datos de prueba y revisa el [estado de la entrega](ESTADO_ENTREGA.md). Evita compartir registros que puedan contener códigos o información privada.
