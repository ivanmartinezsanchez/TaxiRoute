# TaxiRoute

Taxímetro digital en Python desarrollado como proyecto formativo a partir del [enunciado de Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro). El objetivo es avanzar desde una aplicación de terminal hasta un prototipo con historial, interfaz y API, entregando cada fase por separado.

> **Estado: fases 1 y 2 implementadas, probadas e integradas en main.** La aplicación permite gestionar carreras desde la terminal y consultar un historial local.

## Qué problema aborda

TaxiRoute calcula el importe de una carrera según el tiempo transcurrido en dos estados indicados por el conductor: parado y en movimiento. Permite cerrar una carrera y comenzar otra sin reiniciar el programa. La fase 2 añade historial persistente, configuración de tarifas y registros de actividad. La autenticación y las interfaces visuales corresponden a entregas posteriores.

El prototipo usa las tarifas del ejercicio: **0,02 €/s parado** y **0,05 €/s en movimiento**. La velocidad no se medirá automáticamente: el cambio de estado será manual salvo que se acuerde otra cosa. Estas cifras y reglas son datos docentes; no están verificadas como tarifas vigentes ni como un sistema homologado para cobros reales.

## Alcance y entregas

| Fase | Objetivo | Estado |
|---|---|---|
| 1 | Aplicación de terminal: iniciar, cambiar estado, finalizar y repetir carreras | Implementada y probada |
| 2 | Historial persistente, registros, tarifas configurables y pruebas | Completada e integrada |
| 3 | Componentes orientados a objetos, acceso protegido e interfaz táctil para flota | En desarrollo en la rama de fase 3 |
| 4 | Base de datos relacional, API REST, panel web y despliegue con un comando | Componentes experimentales en desarrollo |

Cada entrega requerirá código, una demostración y un tablero GitHub Projects actualizado. El seguimiento se realiza en el [tablero público de TaxiRoute](https://github.com/users/ivanmartinezsanchez/projects/2).

## Requisitos

- Python 3.13, versión utilizada en las pruebas automatizadas de GitHub. La compatibilidad con otras versiones no se da por verificada.
- Git y una cuenta de GitHub para gestionar el código.
- Docker y Docker Compose son opcionales para probar la imagen experimental; no son necesarios para ejecutar la aplicación por terminal.

## Instalación y ejecución

Clona el repositorio y entra en su carpeta. La rama `main` incluye la fase 2, integrada mediante la [pull request #17](https://github.com/ivanmartinezsanchez/TaxiRoute/pull/17).

```bash
git clone https://github.com/ivanmartinezsanchez/TaxiRoute.git
cd TaxiRoute
git switch main
```

Si ya tienes una copia, utiliza su carpeta en lugar de volver a clonar. Ejecuta los comandos siguientes desde la raíz del proyecto, donde están este README y `requirements.txt`.

### Windows con Git Bash

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m taxiroute
```

Si el entorno ya existe, basta con activarlo e instalar las dependencias. En PowerShell, el comando de activación es `.\.venv\Scripts\Activate.ps1`.

### Linux y macOS

Con Python 3.13 instalado, comprueba primero qué versión ejecuta `python3 --version`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m taxiroute
```

Dentro del entorno activado se utiliza `python`. Para salir del entorno después de cerrar TaxiRoute, ejecuta `deactivate`. Las pruebas automatizadas se han superado en los tres sistemas; la comprobación interactiva de la terminal se ha realizado en Windows.

## Interfaz web local de fase 3

La entrega de esta fase es una demostración académica local, no una instalación lista para uso real. El acceso protegido se ha probado con cuentas temporales y, en una prueba separada, con un código enviado a correo real. Por decisión del proyecto no se ha realizado una prueba física en tablet. El [estado de entrega](ESTADO_ENTREGA.md) detalla las comprobaciones y las limitaciones.

El [alcance de acceso protegido y flota](ALCANCE_FASE_3.md) recoge las decisiones del 7 de noviembre de 2026. Hay una demostración experimental con cuentas y HTTPS local. Su panel de administración permite consultar el historial de la flota, filtrar las carreras finalizadas hoy y recorrer los resultados de 100 en 100. Las carreras antiguas sin propietario aparecen como «Sin asignar»; el registro y la asignación de vehículos siguen pendientes. El acceso desde distintas ubicaciones mediante una red privada, con el ordenador del administrador como servidor y una única sesión válida por cuenta, queda aprobado para una entrega posterior. Por ahora se continúa con la versión local.

Hay también una composición experimental para conservar un CSV por conductor en fase 3. Sus pruebas cubren carreras simultáneas, tarifas y aislamiento entre cuentas. Dispone de un servidor HTTPS experimental limitado al propio ordenador, con certificado y clave proporcionados por quien lo ejecuta. La integración OAuth de Gmail utiliza el almacén de Windows y se comprueba al solicitar el código de administrador. Si el correo no está disponible, el servidor puede arrancar y los conductores pueden acceder, pero no se permite el acceso administrativo sin el código. Las pruebas del servidor usaron correo simulado.

El modo durable experimental también permite al administrador consultar el historial CSV de la flota. Si detecta cierres pendientes o datos incoherentes, la consulta no muestra resultados incompletos como definitivos. El modo CSV sin recuperación aún no ofrece esta vista; los archivos antiguos sin propietario no se incluyen automáticamente.

El servidor puede activar de forma explícita un **modo de recuperación experimental** con `--recuperacion-experimental` en una instalación CSV vinculada. Intenta guardar puntos cada cinco segundos y permite revisar y cerrar una carrera interrumpida hasta el último punto fiable, sin añadir el tiempo desconocido. Si una carrera finalizada queda pendiente de guardado y sus datos pueden verificarse, su conductor puede descargar una copia CSV; esta descarga no confirma el guardado en el historial. No garantiza que todos los puntos se hayan guardado ni cubre todas las interrupciones posibles. Se ha probado con datos ficticios, tanto en navegador como mediante pruebas automatizadas. Para consultar las opciones del servidor, ejecuta `python -m taxiroute.servidor_csv_local --help`.

La preparación experimental crea los archivos en una carpeta nueva fuera de Git, pero no configura Gmail ni inicia el servidor. No importa automáticamente historiales anteriores sin propietario. Los permisos efectivos en Windows, la configuración completa de correo y el uso con datos reales siguen pendientes de comprobar; esta composición no es todavía una instalación final.

### Demostración CSV con datos ficticios

Con las dependencias instaladas y el entorno virtual activo, inicia la demostración local con:

```bash
python -m taxiroute.demo_csv
```

Abre `https://127.0.0.1:8768/`. La terminal muestra las cuentas de prueba y el PIN ficticio cuando el administrador lo solicita; no se envía ningún correo. El certificado es autofirmado y el navegador puede mostrar una advertencia. Detén la aplicación con `Ctrl+C`: por defecto, los datos de esta demostración son temporales.

Para conservar exclusivamente datos ficticios y poder revisar una carrera tras reiniciar, añade `--carpeta-demo RUTA_NUEVA`, indicando una carpeta nueva fuera de Git cuyo directorio padre exista. La demo solo reutiliza carpetas que identifica como propias y no sobrescribe una carpeta ajena. Puede cambiarse el puerto local con `--puerto`. Esta demostración no es para producción ni para datos reales.

### Interfaz web anterior sin autenticación

La interfaz web anterior comparte las operaciones con la terminal mediante `ServicioCarreras`. **No tiene autenticación y es distinta de la composición protegida descrita arriba**. Solo debe usarse con datos ficticios y desde el propio ordenador. Con el entorno activado, ejecuta `python -m taxiroute.web --demo` y abre `http://127.0.0.1:8765/`.

Si se ejecuta sin `--demo`, utiliza el historial y los registros de la carpeta `.taxiroute` del usuario, y lee y edita `configuracion/tarifas.json`. Los cambios de ese JSON aparecen en Git.

El modo `--demo` utiliza archivos temporales que desaparecen al detener el servicio. La pantalla identifica el modo activo.

Funciones disponibles:

- Inicio en parado, cambio de estado, importe actualizado y finalización.
- Guardado de carreras y consulta de todas o las finalizadas hoy.
- Edición de tarifas durante una carrera: se aplican a la siguiente; la activa conserva las suyas.
- Reintento del guardado y exportación de la carrera pendiente a un archivo nuevo sin sobrescribir.
- Consulta de los últimos 50 eventos del registro activo; se mantiene la rotación y cinco copias anteriores.
- Ayuda y cierre de la aplicación, bloqueado si hay una carrera activa o pendiente.

Recargar conserva la carrera en memoria del proceso y las pestañas comparten su estado. Cerrar el navegador no detiene el contador. Detener Python puede perder una carrera activa o pendiente. Las carreras ya guardadas permanecen en disco en el modo normal.

Este servicio anterior se limita al propio ordenador y utiliza un servidor de desarrollo. No dispone de autenticación ni sesiones. La fase 3 se ha cerrado como demostración académica local, con las limitaciones recogidas en el estado de entrega.

La revisión del 7 de noviembre de 2026 ha superado 61 pruebas locales. En el navegador se han comprobado el guardado, la consulta, los eventos y la edición de tarifas durante una carrera, aplicadas a la siguiente. Se ha revisado una ventana de 390 píxeles sin desbordamiento horizontal. La validación completa de accesibilidad sigue pendiente.

## Comandos de la aplicación

| Comando | Función |
|---|---|
| `iniciar` | Inicia una carrera en parado y lee las tarifas vigentes. |
| `mover` / `parar` | Cambia el estado del vehículo. |
| `importe` | Consulta el estado y el importe provisional. |
| `finalizar` | Calcula el total e intenta guardar la carrera. |
| `historial` | Consulta todas las carreras guardadas. |
| `historial hoy` | Consulta las carreras finalizadas durante el día local actual. |
| `reintentar` | Reintenta guardar la carrera pendiente. |
| `exportar RUTA` | Crea una copia CSV de la carrera pendiente en un archivo nuevo. |
| `ayuda` | Muestra los comandos disponibles. |
| `salir` | Cierra la aplicación si no hay carrera activa ni pendiente de guardar. |

## Archivos y recuperación

En la aplicación de terminal y en la interfaz web anterior, los datos de uso se guardan en la carpeta `.taxiroute` del usuario, fuera del repositorio. En Windows suele estar bajo `C:\Users\nombre\.taxiroute`; en Linux y macOS, bajo `~/.taxiroute`. La preparación protegida experimental utiliza, en cambio, la carpeta elegida al crearla y guarda los historiales por cuenta.

| Archivo | Contenido |
|---|---|
| `historial.csv` | Identificador, fechas, tiempos, tarifas aplicadas e importe de cada carrera. |
| `actividad.jsonl` | Eventos operativos, uno por línea. |
| `actividad.jsonl.1` a `actividad.jsonl.5` | Registros anteriores, del más reciente al más antiguo. |
| Archivos `.lock` | Coordinación entre ejecuciones; no deben borrarse mientras la aplicación esté en uso. |

Las fechas del historial se conservan en UTC. Para `historial hoy`, se convierte la fecha de finalización al día local del ordenador; la columna mostrada en la lista sigue siendo la fecha de inicio.

Si el guardado falla, TaxiRoute conserva la carrera en memoria y ofrece `reintentar` o `exportar RUTA`. La exportación no sobrescribe archivos existentes ni sustituye el guardado en el historial principal. La carrera continúa pendiente y bloquea el inicio de otra carrera y la salida normal. Cerrar la terminal puede hacer que se pierdan los datos pendientes. Si un archivo está incompleto, se conserva para su revisión; no existe reparación automática ni eliminación de carreras antiguas.

## Pruebas

Con el entorno activado, ejecuta desde la raíz:

```bash
python -m unittest discover -s tests -v
```

La revisión del 6 de noviembre de 2026 ha superado las 46 pruebas en Windows. Cubren cálculo, terminal, historial, configuración, errores de guardado, bloqueos entre procesos y rotación de registros.

[GitHub Actions](https://github.com/ivanmartinezsanchez/TaxiRoute/actions) ejecuta la misma suite con Python 3.13 en Windows, Ubuntu y macOS al subir cambios y en las pull requests. La configuración está en [pruebas.yml](./.github/workflows/pruebas.yml). La [ejecución de la rama](https://github.com/ivanmartinezsanchez/TaxiRoute/actions/runs/42263629496) y la [comprobación de la pull request #17](https://github.com/ivanmartinezsanchez/TaxiRoute/actions/runs/42264111193) han terminado correctamente en los tres sistemas. Estos resultados validan los casos automatizados con Python 3.13; no sustituyen la demostración interactiva ni prueban todos los entornos posibles.

## Configuración de tarifas

Las tarifas se editan en [`configuracion/tarifas.json`](./configuracion/tarifas.json), en euros por segundo y entre comillas, usando punto como separador decimal. Cada valor admite hasta 12 cifras antes del punto y 9 después. El archivo incluido contiene los valores del ejercicio. Tras guardar un cambio, se aplicará al iniciar la siguiente carrera; la que ya esté activa conservará las tarifas con las que comenzó. Si el archivo falta o contiene datos inválidos, TaxiRoute avisará y no iniciará una nueva carrera hasta que se corrija.

El archivo forma parte del repositorio: los cambios locales aparecen en Git y conviene revisarlos antes de incluirlos en un commit.

## Registro de actividad

TaxiRoute registra el arranque, las carreras, los cambios de estado y los errores operativos en formato JSON Lines. Cada línea es un evento con fecha UTC, nivel y tipo; los errores pueden incluir también un código numérico. No se guardan los comandos escritos ni las rutas introducidas. Si el registro falla, se muestra un aviso y la aplicación continúa; cuando se recupera, se informa de que los eventos que fallaron no se guardaron. Un archivo terminado en una línea incompleta se conserva para su revisión y no se modifica automáticamente. La aplicación rota el registro antes de que supere aproximadamente 5 MiB y conserva hasta cinco archivos anteriores; al alcanzar ese número, la copia más antigua se elimina. El historial de carreras no se borra con esta rotación.

## Funcionamiento de una carrera

1. Iniciar una carrera; el taxímetro comienza en estado **parado**.
2. Cambiar entre **parado** y **en movimiento** durante el trayecto.
3. El sistema acumula cada tramo según su duración y tarifa.
4. Finalizar y mostrar el importe en euros con dos decimales.
5. Iniciar otra carrera sin salir de la aplicación.

La fórmula de referencia es `total = tiempo_parado × tarifa_parado + tiempo_movimiento × tarifa_movimiento`. Las duraciones pueden incluir fracciones de segundo. El importe se actualiza automáticamente durante la carrera. El total se redondea a céntimos al finalizar, aplicando el redondeo habitual: si la fracción es exactamente medio céntimo, se redondea hacia arriba. El cálculo conserva la precisión de los tramos hasta el cierre.

## Seguridad, accesibilidad y datos

La versión de terminal y la web anterior funcionan localmente; esta última no tiene autenticación. Existe una demostración web separada con acceso protegido y cuentas ficticias. La interfaz se adapta a distintos tamaños y admite navegación por teclado, aunque aún queda pendiente una revisión completa de accesibilidad. El servicio experimental no está preparado para exponerlo a redes externas ni para gestionar datos reales. La web integrada aplica un máximo compartido de 30 solicitudes de acceso en 60 segundos por proceso; si se alcanza, indica cuánto esperar. Este contador se reinicia al detener el proceso, no se comparte entre procesos y puede afectar a otras cuentas si alguien agota la cuota.

## Servidor SQLite local experimental

La fase 4 dispone de un servidor experimental para instalaciones SQLite vinculadas. Por defecto escucha en `127.0.0.1`. Existe una opción explícita para una IPv4 privada de la red del taxi, con comprobación de dirección, puerto y certificado; esa opción se ha probado mediante simulaciones, sin abrir la red ni usar otro dispositivo. No es el despliegue final. Para ver los parámetros, ejecuta `python -m taxiroute.iniciar_sqlite_local --help`. Requiere cuentas, historial, tarifas y certificado TLS existentes. El correo administrativo se consulta únicamente al solicitar el código y el adaptador actual usa el almacén de Windows.

## Documentación y gestión

- [Mapa de contexto](./MAPA_CONTEXTO.md): decisiones, estado y guía para retomar el proyecto.

- [Documentación técnica](./DOCUMENTACION_TECNICA.md): módulos, decisiones, reglas de cálculo, pruebas y limitaciones.
- [Diseño de migración a SQLite](./DISENO_MIGRACION_FASE_4.md): objetivos, comprobaciones y límites de la fase 4.
- [Revisión de la fase 4](./REVISION_FASE_4.md): requisitos, comprobaciones y pendientes para la entrega.
- [API experimental](./API_EXPERIMENTAL.md): acceso, operaciones de carrera, historial y límites actuales.
- Manual de usuario: se redactará al finalizar, basado en el funcionamiento comprobado.
- [Tablero de GitHub Projects](https://github.com/users/ivanmartinezsanchez/projects/2): tareas organizadas por fase.

## Demostración SQLite con Docker

Con Docker Desktop abierto, desde la carpeta del proyecto ejecuta:

```bash
docker compose -f compose.sqlite-demo.yaml up --build -d
```

Abre `https://127.0.0.1:18769/`. La demo usa únicamente estas cuentas ficticias: conductor `AnaDemo` con contraseña `ConductorDemo123!`, y administrador `AdminDemo` con contraseña `AdminDemo456!`. Después de solicitar el código del administrador, consulta el código ficticio con `docker compose -f compose.sqlite-demo.yaml logs --tail 30`. El historial SQLite de demostración se conserva en un volumen separado entre reinicios. Para detener y retirar el contenedor sin borrar ese volumen, ejecuta `docker compose -f compose.sqlite-demo.yaml down`.

**Solo para datos ficticios.** El certificado es autofirmado y cambia al reiniciar, el navegador puede mostrar una advertencia y el servidor es de desarrollo. El puerto publicado queda limitado al ordenador, aunque otros contenedores de la misma red podrían alcanzar el servicio. No utiliza Gmail ni representa la instalación final para la red del taxi.

## Material original del ejercicio

La carpeta [referencia/proyecto-original](./referencia/proyecto-original) conserva los archivos incluidos en el ZIP del [proyecto de Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro): el enunciado, una imagen, una plantilla de incidencias y el archivo Python inicial. Se mantienen como referencia del punto de partida; la aplicación TaxiRoute se desarrolla en la carpeta `taxiroute`.

El archivo [ENUNCIADO.md](./ENUNCIADO.md) reproduce el enunciado original para facilitar su consulta. El repositorio de origen no declara una licencia en el ZIP revisado; la reutilización de sus materiales queda sujeta a los derechos de sus autores.
## Desarrollo y contribución

Se utilizan ramas para las tareas y mensajes de commit descriptivos en español, siguiendo [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/). No se subirán contraseñas, datos privados, bases de datos reales ni entornos virtuales. Se definirá la política de revisión cuando se organice el equipo.

## Fuentes

- [Enunciado original](https://github.com/Factoria-F5-madrid/project-py-taximetro).
- [Relojes de Python](https://docs.python.org/3.13/library/time.html).
- [Aritmética decimal de Python](https://docs.python.org/3.13/library/decimal.html).
- [WCAG 2.2: referencia rápida](https://www.w3.org/WAI/WCAG22/quickref/).

## Licencia

Pendiente de decidir. La visibilidad pública del repositorio no define por sí sola una licencia de reutilización.

## Acceso protegido y demostración integrada

En la rama de desarrollo hay una demostración HTTPS local que reúne acceso de conductor y administrador, código administrativo ficticio, taxímetro, historial por cuenta, tarifas y gestión básica de conductores. La demostración crea datos temporales y los borra al detenerse; no envía correo real ni debe utilizarse con carreras reales.

El taxímetro de esta demo ofrece una opción para solicitar que la pantalla permanezca encendida durante una carrera. Depende del navegador y del dispositivo; no impide el bloqueo manual ni garantiza que el equipo no se suspenda. La preferencia solo se mantiene mientras la pestaña esté abierta. Sus pruebas simuladas se ejecutan con `node --test tests/test_pantalla_activa.cjs`; por decisión del proyecto no se comprobará en una tablet física para esta entrega.

La aplicación habitual `python -m taxiroute.web` corresponde a la web anterior, sin cuentas. La demostración protegida requiere un certificado y una clave locales de prueba, conservados fuera del repositorio:

```bash
python -m taxiroute.demo_integrada --certificado RUTA_CERTIFICADO --clave RUTA_CLAVE
```

Para probar el recorrido durable experimental se puede añadir `--durable`. En este modo se intenta guardar un punto de control cada cinco segundos mientras la demostración está en marcha. El intervalo no está garantizado y la recuperación interactiva todavía no está disponible; los datos siguen siendo temporales.

Después se abre `https://127.0.0.1:8767/`. El servidor muestra las credenciales ficticias y el código de prueba en su terminal. El navegador puede advertir que el certificado de prueba no es de confianza. El código no debe reutilizarse como configuración de producción.

Una variante adicional permite conservar exclusivamente datos ficticios en una carpeta nueva fuera de Git mediante `--carpeta-demo RUTA`. Tras reiniciar, muestra el último punto fiable y pide confirmación para cerrar la carrera sin añadir el tiempo desconocido. La demo impide que dos instancias cooperativas abran a la vez esa carpeta. Al abrir una demo ficticia anterior reconocida, actualiza su base de cuentas desde versiones compatibles hasta el esquema 14 mediante una transacción, sin generar códigos de recuperación. Una versión antigua de la aplicación puede no reconocer la base actualizada. No está preparada para datos reales y no resuelve todos los tipos de interrupción.

### Demostración integrada con Docker

Con Docker Desktop abierto, desde la raíz del proyecto se puede iniciar la demostración ficticia con:

```bash
docker compose -f compose.demo.yaml up --build
```

La aplicación se abre en `https://127.0.0.1:18767/`. El conductor de prueba es `AnaDemo` y el administrador es `AdminDemo`; sus contraseñas ficticias se encuentran en `taxiroute/demo_integrada.py`. El código ficticio de acceso administrativo aparece en los mensajes de Docker. Para detenerla, pulsa Ctrl+C. Docker conserva sus datos ficticios en un volumen independiente para el siguiente arranque.

Esta opción genera un certificado autofirmado de corta duración que cambia al reiniciar: el navegador avisará de que no es de confianza. El puerto se publica solo en el ordenador, aunque otros contenedores de la misma red Docker podrían alcanzar el servicio. La demostración usa contraseñas conocidas y un PIN visible en los registros, no envía correo real y no debe contener datos ni carreras reales. El servidor es de desarrollo y la recuperación sigue siendo experimental.
El acceso integrado con correo real se ha comprobado en una instalación temporal con certificado local de prueba. Quedan pendientes la recuperación completa, la configuración persistente de uso real y el despliegue de la versión protegida. La comunicación entre ubicaciones distintas y la sesión única por cuenta están acordadas como una evolución posterior. El [estado de entrega](ESTADO_ENTREGA.md) distingue las funciones verificadas de las pendientes.

Las decisiones sobre cuentas, tarifas, permisos y límites de seguridad se detallan en [DISENO_CUENTAS.md](DISENO_CUENTAS.md), [ALCANCE_FASE_3.md](ALCANCE_FASE_3.md) y [DISENO_RECUPERACION.md](DISENO_RECUPERACION.md).
