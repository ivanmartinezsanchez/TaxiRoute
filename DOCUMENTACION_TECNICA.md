# TaxiRoute — Documentación técnica

**Proyecto:** sistema de taxímetro digital  
**Referencia:** TTX-247 · Proyecto formativo de Factoría F5  
**Estado:** fase 1 implementada y probada; fase 2 implementada en su rama de trabajo, pendiente de cierre e integración
**Tecnología principal:** Python

## 1. Introducción

TaxiRoute es un proyecto de software que simula el funcionamiento de un taxímetro. Su finalidad es calcular el importe de una carrera a partir del tiempo transcurrido mientras el vehículo está parado o en movimiento.

El desarrollo está organizado en cuatro fases. La primera entrega proporciona el cálculo y la terminal. La segunda añade historial persistente, tarifas configurables y registros operativos. La interfaz visual, la autenticación, la API y la base de datos corresponden a fases posteriores.

Este documento describe la implementación actual y distingue las funciones disponibles de las previstas. La fase 2 se revisa en la rama `feature/fase-2-historial`; todavía no se ha integrado en `main`.

## 2. Alcance del proyecto

La aplicación está dirigida principalmente a tres perfiles:

- **Conductor:** inicia y finaliza carreras y comunica al sistema si el vehículo está parado o en movimiento.
- **Responsable de flota:** consulta el historial de carreras para revisar la actividad.
- **Personal técnico:** configura las tarifas y utiliza los registros para diagnosticar incidencias.

El cambio de estado se indica manualmente desde la terminal. El proyecto no contempla inicialmente lectura automática de velocidad, GPS ni integración con sistemas de cobro.

Las tarifas del enunciado se utilizan como datos del ejercicio. Al tratarse de un prototipo formativo, su resultado no debe utilizarse para cobrar carreras reales sin verificar previamente las tarifas y los requisitos aplicables.

## 3. Fases de desarrollo

### Fase 1. Taxímetro en terminal

La primera entrega permite realizar una carrera completa desde la terminal. El conductor puede iniciar el servicio, cambiar entre los estados **parado** y **en movimiento**, finalizar la carrera y consultar el importe. Cada carrera comienza en estado **parado**. Una vez cerrada, puede iniciar otra sin reiniciar el programa. La fase 2 añade la condición de resolver el guardado pendiente antes de comenzar el siguiente servicio.

La pantalla inicial explica los comandos disponibles. Durante la carrera, el importe se actualiza automáticamente en pantalla y el sistema muestra mensajes claros cuando una acción no sea válida, por ejemplo, si se intenta finalizar una carrera que no se ha iniciado.

### Fase 2. Historial y registros

La segunda fase incorpora un historial persistente de carreras y un registro de eventos para facilitar el seguimiento de la aplicación. El historial conserva, al menos, la fecha, la duración y el importe de cada servicio, y está disponible en sesiones posteriores.

Las tarifas se leen desde el fichero externo `configuracion/tarifas.json`, sin modificar el código. Los valores se escriben como cadenas decimales no negativas con punto y se convierten a `Decimal`. Para prevenir entradas de tamaño extremo se admiten como máximo 12 cifras enteras y 9 decimales; este límite es técnico y no representa una tarifa real. Se utiliza JSON por ser un formato sencillo para dos valores y estar disponible en la biblioteca estándar. El archivo incluido tiene las tarifas del ejercicio y forma parte del repositorio. Si falta, no se puede leer o contiene datos inválidos, no se inicia una nueva carrera y se comunica el problema. Las pruebas cubren el cálculo, la persistencia, la configuración y errores de lectura y escritura.

#### Registro de actividad

El registro operativo se guarda en `.taxiroute/actividad.jsonl`, dentro de la carpeta de usuario y fuera del repositorio. Se usa JSON Lines: cada línea contiene un evento independiente con fecha UTC, nivel y tipo; algunos eventos incorporan el estado, el tipo de error y, cuando existe, su código numérico. Se registran el arranque, el cierre, el inicio y fin de carreras, los cambios de estado y errores operativos identificados. No se incluyen comandos escritos, rutas introducidas ni el texto de excepciones.

Si no se puede escribir el registro, se avisa en la terminal y la gestión de carreras continúa. Cada evento posterior vuelve a intentar el guardado; al recuperarse se indica que los eventos que fallaron no quedaron registrados. Si el archivo termina con una línea incompleta, se conserva para su revisión. La escritura se coordina mediante un bloqueo auxiliar entre procesos de TaxiRoute; otros programas pueden modificar el archivo sin respetar ese mecanismo. El archivo activo rota al superar el tamaño previsto de 5 MiB con el siguiente evento y se conservan hasta cinco archivos anteriores numerados; cuando es necesario, la copia más antigua se sustituye. La comprobación, la rotación y la escritura se realizan bajo el mismo bloqueo. Si no se puede rotar, se avisa y no se confirma ese evento como guardado. Un evento individual mayor que el límite podría dar lugar a un archivo mayor de 5 MiB. La rotación no afecta al historial de carreras.

#### Historial de carreras

En esta fase se utiliza un archivo CSV para conservar el historial entre sesiones. El formato permite representar cada carrera como una fila y consultar los datos con una hoja de cálculo. Se ha valorado JSON, que facilita estructuras anidadas; para los datos previstos, una tabla resulta suficiente. En la fase 4 el historial deberá migrarse a una base de datos relacional, de acuerdo con el enunciado. La lectura y la escritura del historial se mantienen separadas del cálculo para facilitar ese cambio.

Cada carrera finalizada recoge:

- Un identificador UUID de versión 4 generado para la carrera.
- Las fechas y horas de inicio y finalización.
- La duración total, el tiempo parado y el tiempo en movimiento, en segundos, con sus fracciones cuando corresponda.
- Las tarifas de parado y de movimiento aplicadas, en euros por segundo.
- El importe final en euros, redondeado a céntimos.

El comando `historial` muestra todas las carreras guardadas. `historial hoy` muestra las finalizadas durante el día local actual del ordenador: las fechas se conservan en UTC y se convierten a la zona local antes de comparar el día. Si una fecha de finalización es inválida o no indica zona horaria, se muestra un error para que el archivo pueda revisarse.

Las tarifas se fijan al iniciar cada carrera. Si se cambia la configuración durante una carrera, las nuevas tarifas se aplican a la siguiente. Así el historial conserva los datos necesarios para revisar el cálculo con las tarifas que se utilizaron.

Los archivos de historial y registros se guardan en la carpeta de usuario, fuera del repositorio. El CSV se amplía al finalizar cada carrera; antes de escribir se comprueba su estructura y se busca el identificador para evitar duplicados.

#### Acceso simultáneo al historial

Para coordinar distintas ejecuciones de TaxiRoute se utiliza un archivo auxiliar de bloqueo junto al CSV. Antes de consultar o guardar, cada ejecución solicita el bloqueo; al terminar lo libera. La comprobación del identificador y la escritura se realizan bajo el mismo bloqueo para evitar duplicados cuando varias ejecuciones intentan guardar a la vez. Si el bloqueo no queda disponible en el tiempo previsto, la aplicación informa de que el historial está en uso y mantiene la carrera pendiente para reintentar.

El mecanismo utiliza funciones de la biblioteca estándar específicas de Windows y de sistemas Unix. El archivo auxiliar se conserva y no debe eliminarse mientras haya ejecuciones activas: su presencia por sí sola no indica que el historial esté ocupado. Esta coordinación solo protege a procesos que utilicen el mismo bloqueo; un editor externo puede modificar el CSV sin respetarlo. Las pruebas de procesos se han superado en Windows, Ubuntu y macOS mediante GitHub Actions; también se han realizado comprobaciones locales en Windows y Ubuntu mediante WSL 2. No se han validado todos los tipos de almacenamiento, como las unidades de red. El bloqueo no sustituye a las copias de seguridad ni garantiza la recuperación de escrituras interrumpidas.

#### Si no se puede guardar una carrera

Al finalizar una carrera, la aplicación muestra su importe e intenta guardar los datos en el historial. Si falla, muestra un aviso y explica el motivo cuando puede identificarlo. Si no puede determinar la causa, lo indica sin atribuir el error a un problema concreto.

Mientras la aplicación sigue abierta, conserva temporalmente los datos de la carrera y permite reintentar el guardado. Antes de añadir una carrera al historial, comprueba su identificador para evitar duplicados al reintentar. Una escritura interrumpida puede requerir revisión. No permite iniciar otra carrera ni salir mediante el comando normal mientras exista un guardado pendiente.

Las opciones de ayuda dependen del problema. Si falta espacio, el mensaje propone liberar espacio o usar otra unidad. Si el archivo está bloqueado o no hay permisos, explica qué revisar. `exportar RUTA` permite crear una copia CSV en un archivo nuevo, sin sobrescribir otro. Solo se confirma si la escritura termina correctamente. La copia no incorpora la carrera al historial principal ni elimina su estado pendiente; para eso se utiliza `reintentar`.

Como opción posterior se podrá estudiar el archivo de carreras antiguas: primero se exportarán los datos y se verificará la copia, y únicamente después se ofrecerá su eliminación con una confirmación explícita. No se borrarán carreras automáticamente. Eliminar unas pocas filas de un CSV puede no liberar suficiente espacio para resolver un disco lleno.

Si la aplicación se cierra antes de guardar o exportar la carrera pendiente, los datos conservados en memoria podrían perderse. La interfaz advierte de ese riesgo y no presenta la carrera como guardada hasta comprobar el resultado. El reintento, la copia alternativa y varios casos de error están implementados y cubiertos por pruebas; quedan pendientes más comprobaciones de escrituras interrumpidas y la gestión de archivos antiguos.

### Fase 3. Interfaz y protección de acceso

En esta etapa se organizará la aplicación en componentes con responsabilidades delimitadas y se añadirá un mecanismo de acceso mediante contraseña. Las contraseñas no se almacenarán en texto plano.

Se desarrollará una interfaz adecuada para su uso en una tablet. El conductor deberá poder identificar el estado del vehículo y el importe de un vistazo, interactuar mediante controles táctiles y utilizar la aplicación sin que la pantalla se bloquee durante el cálculo.

### Fase 4. API, panel web y despliegue

La última fase ampliará el sistema con una base de datos relacional, una API REST y un panel web para consultar el historial desde un navegador. La API permitirá iniciar carreras, cambiar su estado, finalizarlas y consultar los datos registrados.

La solución se preparará para desplegarse mediante un único comando, con almacenamiento persistente para conservar los datos después de un reinicio. La elección concreta de herramientas para esta fase se realizará antes de su implementación, en función de los requisitos de despliegue y uso.

## 4. Reglas de cálculo

El precio se calcula a partir del tiempo que el taxi permanece en cada estado:

| Estado | Tarifa del ejercicio |
|---|---:|
| Parado o velocidad inferior a 20 km/h | 0,02 € por segundo |
| En movimiento | 0,05 € por segundo |

Cada vez que se cambia el estado, el sistema calcula el tiempo transcurrido desde el cambio anterior y añade al importe acumulado el coste correspondiente a ese tramo. Al finalizar la carrera, se calcula el último tramo y se muestra el total con dos decimales.

La fórmula general es:

```text
Importe = (tiempo parado × tarifa parado)
        + (tiempo en movimiento × tarifa en movimiento)
```

Por ejemplo, una carrera con 10 segundos parado y 20 segundos en movimiento tendría un importe de **1,20 €**: 0,20 € por el primer tramo y 1,00 € por el segundo. El enunciado no establece una bajada de bandera ni suplementos, por lo que no se incluyen en este cálculo inicial.

### Medición del tiempo e importes

Para medir intervalos se utiliza un reloj monotónico de Python, independiente de los cambios de hora del sistema. Las fechas de inicio y fin se obtienen por separado del reloj del sistema y se guardan en UTC. El tiempo facturado se obtiene de `monotonic_ns`, no de restar esas fechas.

Los importes se representan mediante aritmética decimal para evitar los problemas habituales de representación binaria de cantidades monetarias. El cálculo conserva la precisión de los tramos, incluidas las fracciones de segundo. Al finalizar la carrera, el importe acumulado se redondea una sola vez a céntimos con redondeo habitual: una fracción de exactamente medio céntimo se redondeará hacia arriba. La cantidad mostrada durante la carrera se formatea a dos decimales sin alterar el importe acumulado. Estas reglas están cubiertas por pruebas.

## 5. Organización de la aplicación

La lógica de cálculo está separada de la terminal y del almacenamiento. `GestorTerminal` interpreta los comandos y coordina las operaciones; `Carrera` mide los tramos y calcula el importe sin leer ni escribir archivos.

| Archivo | Responsabilidad |
|---|---|
| `taxiroute/__main__.py` | Inicia la aplicación con `python -m taxiroute`. |
| `taxiroute/terminal.py` | Procesa comandos, presenta mensajes y coordina carrera, configuración, historial y registro. |
| `taxiroute/carrera.py` | Define los estados, el cálculo y el resumen inmutable de una carrera finalizada. |
| `taxiroute/configuracion.py` | Lee y valida las dos tarifas externas. |
| `taxiroute/historial.py` | Consulta y añade filas al CSV; exporta copias de carreras pendientes. |
| `taxiroute/bloqueo.py` | Coordina el acceso a archivos entre procesos mediante bloqueos de Windows o Unix. |
| `taxiroute/registro.py` | Escribe eventos JSON Lines y rota los archivos por tamaño. |
| `tests/` | Contiene las pruebas automatizadas del cálculo y los componentes. |

Al recibir `iniciar`, la terminal carga las tarifas y crea una carrera. Los cambios de estado cierran un tramo y abren el siguiente. `finalizar` construye un `ResumenCarrera` con los datos definitivos y solicita su guardado. Si falla la escritura, ese resumen permanece pendiente en el gestor. El registro de actividad acompaña las operaciones, pero un fallo de registro no impide continuar con la gestión de carreras.

Esta separación permite sustituir el almacenamiento o incorporar otra interfaz en futuras fases sin duplicar las reglas de cálculo. La distribución de responsabilidades se revisará al preparar la interfaz visual.

## 6. Tecnologías

Python es el lenguaje establecido para el proyecto. El entorno de referencia utiliza Python 3.13, también empleado en GitHub Actions. No se afirma compatibilidad comprobada con versiones que no formen parte de esas pruebas.

Para el primer entregable se utiliza la biblioteca estándar de Python para el cálculo y las pruebas. Para la interfaz de terminal se incorpora `prompt_toolkit`, que permite actualizar el importe mientras el conductor escribe sin sobrescribir su entrada. La dependencia se declara en `requirements.txt` y se instala dentro del entorno virtual.

| Necesidad | Enfoque para el proyecto |
|---|---|
| Aplicación inicial | Python y ejecución desde terminal con `prompt_toolkit` |
| Medición de intervalos | Reloj monotónico de Python |
| Cálculo monetario | Módulo `decimal` de Python |
| Pruebas | `unittest`, con relojes controlados y archivos temporales |
| Historial | CSV mediante la biblioteca estándar |
| Tarifas | JSON y conversión a `Decimal` |
| Registro de actividad | JSON Lines y rotación por tamaño |
| Integración continua | GitHub Actions con Python 3.13 |
| Control de versiones | Git y GitHub |
| Organización del trabajo | GitHub Projects, con una columna por fase |
| Interfaz visual, API y base de datos | Herramientas que se elegirán y justificarán al preparar cada fase |

Las alternativas para la interfaz visual, la API y la base de datos se evaluarán en el momento de abordar esas entregas. La decisión tendrá en cuenta el tipo de dispositivo, la facilidad de mantenimiento, las necesidades de acceso y el despliegue.

## 7. Experiencia de uso y accesibilidad

La aplicación debe resultar comprensible para personas con distintos niveles de experiencia técnica. En la terminal, las instrucciones serán visibles desde el inicio y los mensajes describirán tanto el resultado de cada acción como la forma de corregir una entrada incorrecta.

En la interfaz visual se prestará especial atención a:

- Botones y textos suficientemente visibles para su uso en un vehículo.
- Estado de la carrera e importe fáciles de localizar.
- Distribución adaptable a distintos tamaños y orientaciones de pantalla.
- Confirmaciones claras para las acciones que afectan a una carrera.
- Navegación mediante teclado, foco visible y compatibilidad con tecnologías de asistencia.
- Información que no dependa exclusivamente del color.

La accesibilidad se comprobará durante el desarrollo de la interfaz, tomando como referencia los criterios aplicables de WCAG 2.2. No se considerará verificada hasta haber realizado las pruebas correspondientes.

## 8. Seguridad y privacidad

La versión actual funciona de forma local y no requiere contraseñas ni conexión de red para calcular una carrera. En las fases que incorporen acceso protegido y servicios de red se aplicarán, como mínimo, las siguientes medidas:

- Almacenar las contraseñas mediante una función de hash apropiada y una biblioteca mantenida.
- Evitar que contraseñas, claves o información sensible aparezcan en el repositorio y en los registros.
- Validar los datos recibidos por las interfaces y por la API.
- Comprobar los permisos en el servidor para las operaciones protegidas.
- Proteger los datos almacenados y la comunicación de acuerdo con el entorno de despliegue.
- Establecer un procedimiento de copias de seguridad y comprobar su recuperación.

Estas medidas se concretarán y probarán al desarrollar las fases correspondientes. El historial y los registros recogerán únicamente los datos necesarios para la finalidad de la aplicación.

## 9. Pruebas

Las pruebas se ejecutan desde la raíz del proyecto, con el entorno virtual activado:

```bash
python -m unittest discover -s tests -v
```

La suite actual contiene 46 pruebas. Para comprobar duraciones e importes sin esperas reales se inyecta un reloj controlable. Las pruebas de archivos emplean directorios temporales, y las de concurrencia crean procesos que compiten por el mismo bloqueo.

| Área | Comprobaciones |
|---|---|
| Cálculo | Tramos, tiempos fraccionarios, redondeo, estados inválidos y carreras independientes. |
| Terminal | Comandos, ciclo de carrera y actualización de la barra sin borrar la entrada. |
| Historial | Persistencia, identificadores, reintentos sin duplicados y exportación sin sobrescritura. |
| Consulta diaria | Día local de finalización, ausencia de resultados y fechas inválidas. |
| Configuración | Decimales, límites, claves inválidas o repetidas y cambios aplicados a la carrera siguiente. |
| Errores y bloqueos | Archivos incompletos, disco lleno simulado, permisos y acceso entre procesos. |
| Registro | Orden de eventos, contenido, fallo y recuperación, rotación y conservación de copias. |

El flujo `.github/workflows/pruebas.yml` instala las dependencias y ejecuta la suite con Python 3.13 en Windows, Ubuntu y macOS. Se activa con los envíos al repositorio y las pull requests; también declara ejecución manual. Cada trabajo tiene un límite de diez minutos, permisos de lectura del contenido y acciones fijadas a identificadores de commit.

En la revisión del 6 de noviembre de 2026 se han superado las 46 pruebas locales en Windows. La [ejecución de la rama](https://github.com/ivanmartinezsanchez/TaxiRoute/actions/runs/42263629496) y la [ejecución de la pull request #17](https://github.com/ivanmartinezsanchez/TaxiRoute/actions/runs/42264111193) han terminado correctamente en Windows, Ubuntu y macOS con Python 3.13. El resultado de una suite automatizada no sustituye la demostración interactiva en una terminal real.

### Límites de la validación

Las pruebas simulan algunos errores de almacenamiento y comprueban archivos incompletos. No acreditan recuperación frente a cortes eléctricos ni escrituras interrumpidas en cualquier punto. Las carreras pendientes solo permanecen en memoria y los archivos no se reparan automáticamente. El historial se lee completo para consultar y comprobar duplicados; no se ha medido su rendimiento con grandes volúmenes. Los bloqueos no evitan modificaciones de programas externos que no los respeten.

### Guion de demostración de la fase 2

La demostración interactiva utiliza Git Bash dentro de VS Code, con el entorno virtual activado y la terminal situada en la raíz del proyecto. Crea una carrera de prueba en el historial local; no elimina las existentes. Se mantienen las tarifas del ejercicio.

1. Ejecutar `python -m taxiroute`. Introducir `iniciar`, esperar unos segundos, escribir `mover`, volver a esperar y consultar `importe`. Mostrar que la barra se actualiza mientras se escribe.
2. Introducir `finalizar` y comprobar los mensajes del total y del guardado. Consultar `historial hoy` y localizar la nueva carrera por su identificador.
3. Escribir `salir`, ejecutar de nuevo `python -m taxiroute` y consultar `historial`. La misma carrera debe seguir disponible. Cerrar con `salir`.
4. Ejecutar `tail -n 12 ~/.taxiroute/actividad.jsonl` desde Git Bash. Mostrar los eventos de inicio, cambio de estado, finalización, guardado y cierre. Cada línea debe ser un objeto JSON independiente.
5. Mostrar `configuracion/tarifas.json` y ejecutar `python -m unittest discover -s tests -p "test_configuracion.py" -v`. Las pruebas usan archivos temporales y un reloj controlado para comprobar que las nuevas tarifas se aplican a la siguiente carrera, sin cambiar la configuración de uso.
6. Mostrar las verificaciones de GitHub Actions. Para explicar la recuperación ante errores, consultar las pruebas de `test_errores_guardado.py` y `test_historial.py`: simulan fallos y comprueban el reintento y la copia alternativa sin llenar el disco real.

Se han comprobado durante el desarrollo la persistencia entre sesiones, la consulta diaria y los eventos operativos en Windows. Las pruebas automatizadas cubren además los cambios de tarifas y los errores de almacenamiento. Este guion reúne esas comprobaciones para presentar la entrega; su inclusión en el documento no significa que se haya realizado una nueva demostración completa.

### Comprobaciones para cerrar la fase 2

- Mantener las verificaciones automáticas correctas en los tres sistemas hasta la integración.
- Reunir la demostración de persistencia entre sesiones, consulta diaria, cambios de tarifas y registro de eventos.
- Revisar la pull request de la fase y su documentación.
- Integrar la entrega y actualizar el tablero con los resultados verificados.

La autenticación, la interfaz visual, la API y la base de datos tendrán sus propias pruebas en las fases correspondientes. El manual de usuario final se redactará al terminar el proyecto.

## 10. Control de versiones y seguimiento

El código se gestiona con Git y se aloja en un repositorio público de GitHub creado para TaxiRoute. El enunciado original de Factoría F5 se enlaza desde el README.

El trabajo se organiza en un tablero de GitHub Projects con una columna por fase. Las tareas reflejarán los requisitos del proyecto y se actualizarán conforme avance el desarrollo. Los cambios se documentan mediante commits descriptivos en español.

El seguimiento del proyecto está disponible en el [tablero público de TaxiRoute](https://github.com/users/ivanmartinezsanchez/projects/2). No se incluirán en el repositorio contraseñas, claves, entornos virtuales ni datos privados de uso real.

## 11. Documentación del proyecto

El README sirve como punto de entrada al repositorio e incluye el propósito del proyecto, su estado, las instrucciones verificadas de instalación y ejecución y los enlaces a la documentación y al tablero.

Esta documentación técnica acompañará la evolución de la aplicación. Al finalizar el proyecto se redactará un **manual de usuario**, basado en las pantallas y funciones realmente disponibles.

## 12. Referencias

- [Enunciado del proyecto — Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro).
- [Documentación de Python: medición del tiempo](https://docs.python.org/3.13/library/time.html).
- [Documentación de Python: aritmética decimal](https://docs.python.org/3.13/library/decimal.html).
- [Documentación de Python: pruebas con unittest](https://docs.python.org/3.13/library/unittest.html).
- [W3C: guía de referencia WCAG 2.2](https://www.w3.org/WAI/WCAG22/quickref/).
- [OWASP: almacenamiento de contraseñas](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
