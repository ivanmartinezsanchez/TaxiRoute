# Guía de arranque de TaxiRoute en Windows

Hay dos formas de ejecutar TaxiRoute. Para la presentación académica, usa la demostración ficticia. El lanzador protegido es experimental y solo sirve con una instalación que ya se haya preparado: no instala automáticamente las cuentas, los certificados ni los datos iniciales.

## Primer arranque de una instalación propia: candidato experimental

Se ha preparado `iniciar-taxiroute.cmd` para iniciar el asistente sin escribir las tres rutas. La primera vez, el asistente solicita el usuario y correo del administrador y una contraseña temporal que se introduce sin mostrarla. También muestra cinco códigos que debes guardar en un lugar privado y confirmar; sirven para desbloquear la cuenta junto con la contraseña, no sustituyen la contraseña ni el código de correo. Después crea una instalación SQLite nueva y un certificado local autofirmado. No importa ni modifica instalaciones anteriores.

El archivo está preparado para ejecutarse desde Windows con Docker Desktop abierto, el entorno `.venv` y Gmail ya autorizados. Por defecto utiliza `%LOCALAPPDATA%/TaxiRoute-MVP`. Si la carpeta ya existe pero no es una instalación completa reconocida, se detiene sin sobrescribirla. Si se interrumpe la preparación, conserva la carpeta y los códigos mostrados y revisa el estado antes de repetir. En el primer acceso se pedirá verificar el correo y cambiar la contraseña temporal.

**Estado:** este primer arranque se ha probado con entradas y cuentas ficticias y Docker; el archivo de Windows todavía no se ha probado de forma interactiva. No uses datos ni cobros reales: faltan la comprobación de permisos efectivos de los archivos en Windows, una configuración de servidor y TLS definitiva, copias/restauración y acceso seguro a la red del taxi. El certificado de prueba caduca a los 90 días y no se renueva automáticamente. No confundas esta instalación propia en preparación con la demo de la siguiente sección.

## Opción 1: demostración de la entrega

1. Abre **Docker Desktop** y espera a que indique que está funcionando.
2. Abre **VS Code** en la carpeta del proyecto y abre una terminal Git Bash. Comprueba que estás en la raíz, donde se encuentra `compose.sqlite-demo.yaml`.
3. La demostración está en la rama `feature/fase-4-produccion`. Si acabas de clonar el proyecto, cambia a ella con `git switch feature/fase-4-produccion`.
4. Ejecuta:

   ```bash
   docker compose -f compose.sqlite-demo.yaml up --build -d
   ```

5. Abre `https://127.0.0.1:18769/`. El navegador puede advertir de que el certificado local es autofirmado; decide si deseas continuar con esta demostración.
6. Utiliza solo las cuentas ficticias del [manual de usuario](MANUAL_USUARIO.md). El código del administrador aparece en los registros y no se envía por Gmail.

Para terminar, finaliza las carreras y comprueba su guardado; después ejecuta `docker compose -f compose.sqlite-demo.yaml down`. El volumen de la demo se conserva. Consulta el manual para el recorrido completo y la solución de problemas.

## Opción 2: lanzador protegido experimental

Esta opción usa el puerto **18770** y puede enviar el PIN del administrador por Gmail. **Es experimental y solo debe usarse con datos de prueba.** Existe un candidato de primer arranque descrito arriba, pero todavía no se ha validado una instalación final; no es el despliegue definitivo de un comando solicitado en el enunciado.

Antes necesitas:

- Docker Desktop funcionando y el entorno virtual del proyecto instalado.
- Una carpeta de instalación SQLite vinculada, ya preparada, con `cuentas.sqlite3`, `historial.sqlite3` y `tarifas.json`.
- Un certificado y su clave TLS ya preparados para `127.0.0.1`.
- La autorización alternativa de Gmail ya guardada en el Administrador de credenciales de Windows.

Desde la raíz del proyecto, activa el entorno virtual en Git Bash:

```bash
source .venv/Scripts/activate
```

Después ejecuta el comando siguiente sustituyendo cada ruta por la ubicación **real** de tu instalación. Las rutas indicadas son ejemplos: no funcionan hasta que prepares esos archivos.

```bash
python -m taxiroute.arrancar_windows --datos 'C:/RUTA/INSTALACION' --certificado 'C:/RUTA/certificado.pem' --clave 'C:/RUTA/clave.pem'
```

El lanzador comprueba las rutas y Docker, arranca la instalación si hace falta, carga la autorización de Gmail si no hay una ya cargada y abre `https://127.0.0.1:18770/`. No recrea un contenedor existente ni detiene una carrera. Si ya hay una autorización temporal, informa de ello sin afirmar que siga vigente o a qué cuenta pertenece.

La comprobación HTTPS del lanzador confía en el certificado indicado solo para comprobar el servicio; **no instala confianza en el navegador**. Si el certificado es autofirmado, el navegador puede mostrar una advertencia.

Para volver a abrir la aplicación después de detener o reiniciar el contenedor, ejecuta el mismo comando. La autorización temporal desaparece al reiniciar y el lanzador intenta cargarla de nuevo. Si el contenedor se reinicia por sí solo mientras nadie ejecuta el lanzador, el acceso administrativo no funcionará hasta recargarla. Una autorización revocada o caducada puede requerir que vuelvas a autorizar Gmail.

Antes de detener la instalación, finaliza las carreras y comprueba que se han guardado. Para detener el contenedor de esta configuración concreta:

```bash
docker stop --timeout 10 taxiroute-temporal-experimental-taxiroute-1
```

Este comando no borra la carpeta de datos. La parada bajo carga no está validada. Si el lanzador informa de un error, no borres ni recrees la instalación: revisa que Docker esté abierto, que las tres rutas correspondan a la instalación y que Gmail siga autorizado. Si el servicio quedó arrancado pero falló la carga del correo, el mensaje lo indica y puedes consultar la situación antes de volver a intentarlo.

La aplicación está publicada solo en el ordenador. El acceso desde otro dispositivo de la red del taxi, la confianza TLS para ese dispositivo y la instalación para datos reales siguen pendientes. La autorización temporal puede ser accesible a administradores del equipo, al daemon Docker y a procesos del mismo usuario del contenedor; tmpfs no garantiza ausencia de swap o volcados.


## Opción 3: instalación local con proxy experimental

Esta variante arranca la instalación local en Docker mediante un proxy y conserva los datos de prueba entre arranques. Se ha comprobado en Windows con el acceso de administrador y correo autorizado. Sigue siendo experimental: no está validada para cobros ni datos reales, copias y restauración completas o acceso desde otros dispositivos.

1. Abre Docker Desktop y espera a que esté funcionando.
2. Abre una terminal de Windows en la carpeta del proyecto. Comprueba que existe el entorno virtual `.venv` y que el perfil de Gmail ya está autorizado en Windows.
3. Ejecuta:

   ```cmd
   iniciar-taxiroute-proxy-experimental.cmd --conectar-gmail
   ```

4. Accede a `https://127.0.0.1:18771/`. El certificado es autofirmado, por lo que el navegador puede mostrar una advertencia.
5. Entra con tus propias credenciales. El código de verificación se envía al correo configurado para el administrador; no lo compartas. Guarda también los códigos de recuperación en privado.

El primer arranque solicita los datos del administrador. Los siguientes intentan reutilizar la instalación sin recrearla. La opción `--conectar-gmail` solicita expresamente cargar el perfil autorizado cuando falta; si ya existe un archivo temporal, el lanzador avisa de que no ha comprobado su identidad ni vigencia. El acceso administrativo puede fallar si la autorización ha caducado o ha sido revocada.

Antes de detener Docker, finaliza las carreras y comprueba que se hayan guardado. Una interrupción puede requerir revisión: la aplicación ofrece cerrar al último punto fiable sin cobrar automáticamente el intervalo desconocido. La recuperación se ha probado con carreras ficticias y no equivale a garantizar una recuperación completa de datos reales. Si el arranque informa de un error, conserva la carpeta y revisa el problema antes de borrar o recrear nada.
