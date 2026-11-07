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
| 3 | Componentes orientados a objetos, acceso con contraseña e interfaz táctil | Pendiente |
| 4 | Base de datos relacional, API REST, panel web y despliegue con un comando | Pendiente |

Cada entrega requerirá código, una demostración y un tablero GitHub Projects actualizado. El seguimiento se realiza en el [tablero público de TaxiRoute](https://github.com/users/ivanmartinezsanchez/projects/2).

## Requisitos

- Python 3.13, versión utilizada en las pruebas automatizadas de GitHub. La compatibilidad con otras versiones no se da por verificada.
- Git y una cuenta de GitHub para gestionar el código.
- Docker y Docker Compose se evaluarán para la fase de despliegue; todavía no son necesarios para el MVP.

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

## Prototipo web local de fase 3

En la rama `feature/fase-3-web-local` se está preparando la interfaz con Flask. Con el entorno activado y las dependencias instaladas:

```bash
python -m pip install -r requirements.txt
python -m taxiroute.web
```

Abre [TaxiRoute local](http://127.0.0.1:8765/) en el navegador. El puerto del prototipo es 8765; el servicio escucha solo en el propio ordenador y usa el servidor de desarrollo sin depurador. Para detenerlo, pulsa Ctrl+C en la terminal donde lo arrancaste.

Esta prueba utiliza las tarifas del ejercicio incluidas en `Carrera` (0,02 y 0,05 €/s). No lee las tarifas personalizadas ni escribe en el historial o registro de uso. Permite probar el inicio, los cambios de estado, la finalización y la actualización del importe.

El prototipo mantiene una sola carrera en memoria por proceso, compartida por las pestañas. Recargar la página conserva su estado; cerrar el navegador no finaliza la carrera. Al detener Python se pierde el estado de prueba. Estos comportamientos son provisionales y deben acordarse antes de conectar la interfaz con los datos persistentes.

Todavía no incorpora autenticación. La revisión local del 7 de noviembre de 2026 ha superado 52 pruebas, incluidas seis del servidor web. Se ha probado el ciclo en el navegador y una ventana de 390 píxeles sin desbordamiento horizontal. Esto no acredita compatibilidad con todos los dispositivos ni una auditoría completa de accesibilidad.

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

Los datos de uso se guardan en la carpeta `.taxiroute` del usuario, fuera del repositorio. En Windows suele estar bajo `C:\Users\nombre\.taxiroute`; en Linux y macOS, bajo `~/.taxiroute`.

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

La versión actual funciona localmente y no incorpora autenticación ni servicios de red. En fases posteriores se diseñarán autenticación, protección del historial y controles de acceso antes de exponer la aplicación. La interfaz visual tendrá objetivos de accesibilidad, adaptación a distintos tamaños de pantalla y navegación mediante teclado; su conformidad se comprobará cuando exista.

## Documentación y gestión

- [Documentación técnica](./DOCUMENTACION_TECNICA.md): módulos, decisiones, reglas de cálculo, pruebas y limitaciones.
- Manual de usuario: se redactará al finalizar, basado en el funcionamiento comprobado.
- [Tablero de GitHub Projects](https://github.com/users/ivanmartinezsanchez/projects/2): tareas organizadas por fase.

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
