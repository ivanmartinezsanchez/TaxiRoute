# TaxiRoute — Documentación técnica

**Proyecto:** sistema de taxímetro digital  
**Referencia:** TTX-247 · Proyecto formativo de Factoría F5  
**Estado:** fase 1 implementada y probada; fase 2 en desarrollo
**Tecnología principal:** Python

## 1. Introducción

TaxiRoute es un proyecto de software que simula el funcionamiento de un taxímetro. Su finalidad es calcular el importe de una carrera a partir del tiempo transcurrido mientras el vehículo está parado o en movimiento.

El desarrollo está organizado en cuatro fases. La primera se centrará en validar el cálculo mediante una aplicación de terminal. Después se incorporarán el historial de carreras, la configuración de tarifas, una interfaz visual y, por último, una API y un panel web.

Este documento describe el alcance, la organización prevista y los criterios que guiarán el desarrollo. No representa una aplicación ya terminada: las funcionalidades se irán implementando y comprobando en cada entrega.

## 2. Alcance del proyecto

La aplicación está dirigida principalmente a tres perfiles:

- **Conductor:** inicia y finaliza carreras y comunica al sistema si el vehículo está parado o en movimiento.
- **Responsable de flota:** consulta el historial de carreras para revisar la actividad.
- **Personal técnico:** configura las tarifas y utiliza los registros para diagnosticar incidencias.

En la primera fase, el cambio de estado será manual. El proyecto no contempla inicialmente lectura automática de velocidad, GPS ni integración con sistemas de cobro.

Las tarifas del enunciado se utilizan como datos del ejercicio. Al tratarse de un prototipo formativo, su resultado no debe utilizarse para cobrar carreras reales sin verificar previamente las tarifas y los requisitos aplicables.

## 3. Fases de desarrollo

### Fase 1. Taxímetro en terminal

La primera entrega permitirá realizar una carrera completa desde la terminal. El conductor podrá iniciar el servicio, cambiar entre los estados **parado** y **en movimiento**, finalizar la carrera y consultar el importe. Cada carrera comenzará en estado **parado**. Una vez cerrada, podrá iniciar otra sin reiniciar el programa.

La pantalla inicial explicará los comandos disponibles. Durante la carrera, el importe se actualizará automáticamente en pantalla y el sistema mostrará mensajes claros cuando una acción no sea válida, por ejemplo, si se intenta finalizar una carrera que no se ha iniciado.

### Fase 2. Historial y registros

La segunda fase incorporará un historial persistente de carreras y un registro de eventos para facilitar el seguimiento de la aplicación. El historial conservará, al menos, la fecha, la duración y el importe de cada servicio, y estará disponible en sesiones posteriores.

Las tarifas se leen desde el fichero externo `configuracion/tarifas.json`, sin modificar el código. Los valores se escriben como cadenas decimales no negativas con punto y se convierten a `Decimal`. Para prevenir entradas de tamaño extremo se admiten como máximo 12 cifras enteras y 9 decimales; este límite es técnico y no representa una tarifa real. Se utiliza JSON por ser un formato sencillo para dos valores y estar disponible en la biblioteca estándar. El archivo incluido tiene las tarifas del ejercicio y forma parte del repositorio. Si falta, no se puede leer o contiene datos inválidos, no se inicia una nueva carrera y se comunica el problema. Las pruebas cubren el cálculo, la persistencia, la configuración y errores de lectura y escritura.

#### Historial de carreras

En esta fase se utilizará un archivo CSV para conservar el historial entre sesiones. El formato permite representar cada carrera como una fila y consultar los datos con una hoja de cálculo. Se ha valorado JSON, que facilita estructuras anidadas; para los datos previstos, una tabla resulta suficiente. En la fase 4 el historial deberá migrarse a una base de datos relacional, de acuerdo con el enunciado. La lectura y la escritura del historial se mantendrán separadas del cálculo para facilitar ese cambio.

Cada carrera finalizada recogerá:

- Un identificador de carrera.
- Las fechas y horas de inicio y finalización.
- La duración total, el tiempo parado y el tiempo en movimiento, en segundos, con sus fracciones cuando corresponda.
- Las tarifas de parado y de movimiento aplicadas, en euros por segundo.
- El importe final en euros, redondeado a céntimos.

El comando `historial` muestra todas las carreras guardadas. `historial hoy` muestra las finalizadas durante el día local actual del ordenador: las fechas se conservan en UTC y se convierten a la zona local antes de comparar el día. Si una fecha de finalización es inválida o no indica zona horaria, se muestra un error para que el archivo pueda revisarse.

Las tarifas se fijarán al iniciar cada carrera. Si se cambia la configuración durante una carrera, las nuevas tarifas se aplicarán a la siguiente. Así el historial conservará los datos necesarios para revisar el cálculo con las tarifas que se utilizaron.

Los archivos de historial y registros contendrán únicamente la información necesaria y quedarán fuera del repositorio. Los detalles de formato y consulta se concretarán antes de implementar cada funcionalidad.

#### Acceso simultáneo al historial

Para coordinar distintas ejecuciones de TaxiRoute se utiliza un archivo auxiliar de bloqueo junto al CSV. Antes de consultar o guardar, cada ejecución solicita el bloqueo; al terminar lo libera. La comprobación del identificador y la escritura se realizan bajo el mismo bloqueo para evitar duplicados cuando varias ejecuciones intentan guardar a la vez. Si el bloqueo no queda disponible en el tiempo previsto, la aplicación informa de que el historial está en uso y mantiene la carrera pendiente para reintentar.

El mecanismo utiliza funciones de la biblioteca estándar específicas de Windows y de sistemas Unix. El archivo auxiliar se conserva y no debe eliminarse mientras haya ejecuciones activas: su presencia por sí sola no indica que el historial esté ocupado. Esta coordinación solo protege a procesos que utilicen el mismo bloqueo; un editor externo puede modificar el CSV sin respetarlo. Las pruebas de procesos se han ejecutado en Windows; quedan por comprobar en otros sistemas y en distintos tipos de almacenamiento. El bloqueo no sustituye a las copias de seguridad ni garantiza la recuperación de escrituras interrumpidas.

#### Si no se puede guardar una carrera

Cuando una carrera finalice, la aplicación mostrará su importe y tratará de guardar los datos en el historial. Si el guardado falla, mostrará un aviso claro y explicará el motivo cuando pueda identificarlo. Si no puede determinar la causa, lo indicará sin atribuir el error a un problema concreto.

Mientras la aplicación siga abierta, conservará temporalmente los datos de la carrera y ofrecerá reintentar el guardado. Antes de añadir una carrera al historial, se comprobará su identificador para evitar duplicados al reintentar, teniendo en cuenta que una escritura interrumpida puede requerir revisión. No se iniciará otra carrera mientras la anterior esté pendiente de resolver.

Las opciones de ayuda dependerán del problema. Si falta espacio de almacenamiento, se indicará cómo liberarlo y se ofrecerá guardar una copia de los datos de la carrera en otra ubicación. Si el archivo está bloqueado o no hay permisos, se explicará qué revisar. La copia alternativa también puede fallar: solo se confirmará cuando se haya completado correctamente.

Como opción posterior se podrá estudiar el archivo de carreras antiguas: primero se exportarán y verificará la copia, y únicamente después se ofrecerá su eliminación con una confirmación explícita. No se borrarán carreras automáticamente. Eliminar unas pocas filas de un CSV puede no liberar suficiente espacio para resolver un disco lleno.

Si la aplicación se cierra antes de guardar o exportar la carrera pendiente, los datos conservados en memoria podrían perderse. La interfaz advertirá de ese riesgo y no presentará la carrera como guardada hasta comprobar el resultado. El reintento, la copia alternativa y varios casos de error están implementados y cubiertos por pruebas; quedan pendientes más comprobaciones de escrituras interrumpidas y la gestión de archivos antiguos.

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

Para medir intervalos se utiliza un reloj monotónico de Python, independiente de los cambios de hora del sistema. La fecha y hora de inicio se tratarán por separado cuando se incorpore el historial.

Los importes se representarán mediante aritmética decimal para evitar los problemas habituales de representación binaria de cantidades monetarias. El cálculo conservará la precisión de los tramos, incluidas las fracciones de segundo. Al finalizar la carrera, el importe acumulado se redondeará una sola vez a céntimos con redondeo habitual: una fracción de exactamente medio céntimo se redondeará hacia arriba. La cantidad mostrada durante la carrera se formateará a dos decimales sin alterar el importe acumulado. Estas reglas quedarán cubiertas por pruebas.

## 5. Organización de la aplicación

La aplicación se estructurará alrededor de la lógica de la carrera. Esa lógica se mantendrá separada de la interfaz que utilice el conductor y de los mecanismos empleados para guardar información. Así, el mismo cálculo podrá aprovecharse desde la terminal y desde las interfaces que se desarrollen en fases posteriores.

```text
Terminal / interfaz visual / API
               │
        Gestión de carreras
               │
      Cálculo de tiempo e importe
               │
    Configuración e historial de datos
```

La organización definitiva de archivos y clases se ajustará a medida que avance el desarrollo. Se buscará que cada componente tenga una responsabilidad clara, sin añadir capas innecesarias en la primera versión.

## 6. Tecnologías

Python es el lenguaje establecido para el proyecto. El entorno de desarrollo utilizará Python 3.13, que ya está instalado en el equipo y cumple el requisito mínimo de Python 3.10 indicado en el enunciado.

Para el primer entregable se utiliza la biblioteca estándar de Python para el cálculo y las pruebas. Para la interfaz de terminal se incorpora `prompt_toolkit`, que permite actualizar el importe mientras el conductor escribe sin sobrescribir su entrada. La dependencia se declara en `requirements.txt` y se instala dentro del entorno virtual.

| Necesidad | Enfoque para el proyecto |
|---|---|
| Aplicación inicial | Python y ejecución desde terminal con `prompt_toolkit` |
| Medición de intervalos | Reloj monotónico de Python |
| Cálculo monetario | Módulo `decimal` de Python |
| Pruebas iniciales | Herramientas de la biblioteca estándar |
| Control de versiones | Git y GitHub |
| Organización del trabajo | GitHub Projects, con una columna por fase |
| Historial, interfaz, API y base de datos | Herramientas que se elegirán y justificarán al preparar cada fase |

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

La primera fase funcionará de forma local y no requiere contraseñas ni conexión de red para calcular una carrera. En las fases que incorporen acceso protegido y servicios de red se aplicarán, como mínimo, las siguientes medidas:

- Almacenar las contraseñas mediante una función de hash apropiada y una biblioteca mantenida.
- Evitar que contraseñas, claves o información sensible aparezcan en el repositorio y en los registros.
- Validar los datos recibidos por las interfaces y por la API.
- Comprobar los permisos en el servidor para las operaciones protegidas.
- Proteger los datos almacenados y la comunicación de acuerdo con el entorno de despliegue.
- Establecer un procedimiento de copias de seguridad y comprobar su recuperación.

Estas medidas se concretarán y probarán al desarrollar las fases correspondientes. El historial y los registros recogerán únicamente los datos necesarios para la finalidad de la aplicación.

## 9. Pruebas

Las pruebas automáticas servirán para comprobar el comportamiento de la aplicación y detectar errores al introducir cambios. Para la primera fase se cubrirán, al menos:

- Inicio y finalización de una carrera.
- Cálculo de tramos en parado y en movimiento.
- Cambios repetidos de estado y tiempos de duración cero o fraccionaria.
- Formato y redondeo del importe final.
- Intentos de realizar acciones fuera del estado correspondiente.
- Inicio de varias carreras durante una misma ejecución.

En las fases posteriores se añadirán pruebas de persistencia, configuración, autenticación, accesibilidad, API y recuperación de datos. Para verificar el cálculo sin esperas innecesarias se utilizará un reloj controlable en las pruebas.

Cada fase incluirá una demostración de su funcionamiento. La demostración complementa las pruebas automáticas, pero no las sustituye.

## 10. Control de versiones y seguimiento

El código se gestionará con Git y se alojará en un repositorio público de GitHub creado para TaxiRoute. El enunciado original de Factoría F5 se enlazará desde el README.

El trabajo se organizará en un tablero de GitHub Projects con una columna por fase. Las tareas reflejarán los requisitos del proyecto y se actualizarán conforme avance el desarrollo. Los cambios se documentarán mediante commits descriptivos.

El seguimiento del proyecto está disponible en el [tablero público de TaxiRoute](https://github.com/users/ivanmartinezsanchez/projects/2). No se incluirán en el repositorio contraseñas, claves, entornos virtuales ni datos privados de uso real.

## 11. Documentación del proyecto

El README servirá como punto de entrada al repositorio e incluirá el propósito del proyecto, su estado, las instrucciones verificadas de instalación y ejecución y los enlaces a la documentación y al tablero.

Esta documentación técnica acompañará la evolución de la aplicación. Al finalizar el proyecto se redactará un **manual de usuario**, basado en las pantallas y funciones realmente disponibles.

## 12. Referencias

- [Enunciado del proyecto — Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro).
- [Documentación de Python: medición del tiempo](https://docs.python.org/3.13/library/time.html).
- [Documentación de Python: aritmética decimal](https://docs.python.org/3.13/library/decimal.html).
- [Documentación de Python: pruebas con unittest](https://docs.python.org/3.13/library/unittest.html).
- [W3C: guía de referencia WCAG 2.2](https://www.w3.org/WAI/WCAG22/quickref/).
- [OWASP: almacenamiento de contraseñas](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
