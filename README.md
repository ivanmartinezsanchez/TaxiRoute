# TaxiRoute

Taxímetro digital en Python desarrollado como proyecto formativo a partir del [enunciado de Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro). El objetivo es avanzar desde una aplicación de terminal hasta un prototipo con historial, interfaz y API, entregando cada fase por separado.

> **Estado: fase 1 implementada y probada; fase 2 en desarrollo.** La aplicación permite gestionar carreras desde la terminal y consultar un historial local.

## Qué problema aborda

TaxiRoute calcula el importe de una carrera según el tiempo transcurrido en dos estados indicados por el conductor: parado y en movimiento. Permite cerrar una carrera y comenzar otra sin reiniciar el programa. Las fases siguientes incorporarán historial, configuración, autenticación e interfaces.

El prototipo usa las tarifas del ejercicio: **0,02 €/s parado** y **0,05 €/s en movimiento**. La velocidad no se medirá automáticamente: el cambio de estado será manual salvo que se acuerde otra cosa. Estas cifras y reglas son datos docentes; no están verificadas como tarifas vigentes ni como un sistema homologado para cobros reales.

## Alcance y entregas

| Fase | Objetivo | Estado |
|---|---|---|
| 1 | Aplicación de terminal: iniciar, cambiar estado, finalizar y repetir carreras | Implementada y probada |
| 2 | Historial persistente, registros, tarifas configurables y pruebas | En curso |
| 3 | Componentes orientados a objetos, acceso con contraseña e interfaz táctil | Pendiente |
| 4 | Base de datos relacional, API REST, panel web y despliegue con un comando | Pendiente |

Cada entrega requerirá código, una demostración y un tablero GitHub Projects actualizado. El seguimiento se realiza en el [tablero público de TaxiRoute](https://github.com/users/ivanmartinezsanchez/projects/2).

## Requisitos previstos

- Python 3.13 para el desarrollo de esta formación; el enunciado acepta Python 3.10 o superior.
- Git y una cuenta de GitHub para gestionar el código.
- Docker y Docker Compose se evaluarán para la fase de despliegue; todavía no son necesarios para el MVP.

## Instalación y ejecución

Para preparar el entorno y ejecutar TaxiRoute desde Git Bash, sitúate en la carpeta del proyecto:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m taxiroute
```

Para ejecutar las pruebas:

```bash
python -m unittest discover -s tests -v
```

Durante la ejecución, escribe `ayuda` para ver los comandos. Usa `iniciar`, `mover`, `parar`, `importe` y `finalizar`; para consultar todas las carreras guardadas, escribe `historial`; para ver las finalizadas durante el día local actual, escribe `historial hoy`. El archivo se almacena en `.taxiroute/historial.csv` dentro de la carpeta de usuario. Si el guardado falla, la aplicación conserva temporalmente la carrera y permite usar `reintentar` o `exportar RUTA` para crear una copia en otra ubicación. La copia no sustituye al guardado en el historial principal. Si se cierra la terminal antes de resolver el error, los datos pendientes podrían perderse. El historial guarda las fechas en UTC y determina el día local usando la fecha de finalización de cada carrera. Las tarifas configurables y los registros operativos siguen pendientes.

## Funcionamiento previsto de una carrera

1. Iniciar una carrera; el taxímetro comienza en estado **parado**.
2. Cambiar entre **parado** y **en movimiento** durante el trayecto.
3. El sistema acumula cada tramo según su duración y tarifa.
4. Finalizar y mostrar el importe en euros con dos decimales.
5. Iniciar otra carrera sin salir de la aplicación.

La fórmula de referencia es `total = tiempo_parado × tarifa_parado + tiempo_movimiento × tarifa_movimiento`. Las duraciones pueden incluir fracciones de segundo. El importe se actualiza automáticamente durante la carrera. El total se redondea a céntimos al finalizar, aplicando el redondeo habitual: si la fracción es exactamente medio céntimo, se redondea hacia arriba. El cálculo conservará la precisión de los tramos hasta el cierre.

## Seguridad, accesibilidad y datos

El MVP no manejará contraseñas ni publicará servicios de red. En fases posteriores se diseñarán autenticación, protección del historial y controles de acceso antes de exponer la aplicación. La interfaz visual tendrá objetivos de accesibilidad, adaptación a distintos tamaños de pantalla y navegación mediante teclado; su conformidad se comprobará cuando exista.

## Documentación y gestión

- [Documentación técnica inicial](./DOCUMENTACION_TECNICA.md): arquitectura propuesta, decisiones, reglas y plan de pruebas.
- Manual de usuario: se redactará al finalizar, basado en el funcionamiento comprobado.
- [Tablero de GitHub Projects](https://github.com/users/ivanmartinezsanchez/projects/2): tareas organizadas por fase.

## Material original del ejercicio

La carpeta [referencia/proyecto-original](./referencia/proyecto-original) conserva los archivos incluidos en el ZIP del [proyecto de Factoría F5](https://github.com/Factoria-F5-madrid/project-py-taximetro): el enunciado, una imagen, una plantilla de incidencias y el archivo Python inicial. Se mantienen como referencia del punto de partida; la aplicación TaxiRoute se desarrolla en la carpeta `taxiroute`.

El archivo [ENUNCIADO.md](./ENUNCIADO.md) reproduce el enunciado original para facilitar su consulta. El repositorio de origen no declara una licencia en el ZIP revisado; la reutilización de sus materiales queda sujeta a los derechos de sus autores.
## Desarrollo y contribución

Se utilizarán ramas para tareas y mensajes de commit descriptivos, preferiblemente [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/). No se subirán contraseñas, datos privados, bases de datos reales ni entornos virtuales. Se definirá la política de revisión cuando se organice el equipo.

## Fuentes

- [Enunciado original](https://github.com/Factoria-F5-madrid/project-py-taximetro).
- [Relojes de Python](https://docs.python.org/3.13/library/time.html).
- [Aritmética decimal de Python](https://docs.python.org/3.13/library/decimal.html).
- [WCAG 2.2: referencia rápida](https://www.w3.org/WAI/WCAG22/quickref/).

## Licencia

Pendiente de decidir. La visibilidad pública del repositorio no define por sí sola una licencia de reutilización.
