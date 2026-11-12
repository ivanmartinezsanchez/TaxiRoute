# Guía de arranque de TaxiRoute en Windows

Hay dos formas de ejecutar TaxiRoute. Para la presentación académica, usa la demostración ficticia. El lanzador protegido es experimental y solo sirve con una instalación que ya se haya preparado: no instala automáticamente las cuentas, los certificados ni los datos iniciales.

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

Esta opción usa el puerto **18770** y puede enviar el PIN del administrador por Gmail. **Es experimental y solo debe usarse con datos de prueba.** Todavía no existe un asistente que prepare automáticamente una instalación final; no es el despliegue definitivo de un comando solicitado en el enunciado.

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
