# Migración del historial en la fase 4

## Objetivo

Pasar el historial de las carreras de los archivos CSV de la fase 3 a SQLite, manteniendo la cuenta de cada conductor, la precisión de los datos y los archivos originales. El cambio corresponde a la fase 4 y se preparará y probará primero en instalaciones temporales.

## Elección de almacenamiento

SQLite permite utilizar una base relacional local sin instalar un servidor adicional y ya se emplea para las cuentas. El historial usa una base independiente para mantener separados sus datos y sus responsabilidades. CSV se mantiene como origen y respaldo de la fase anterior; no facilita las mismas consultas y controles relacionales. Un servidor como PostgreSQL añadiría instalación y operación que no se necesitan para esta primera entrega local. Si se requieren varios servidores o un volumen y una concurrencia que SQLite no soporte adecuadamente, se revisará esta elección.

## Pasos de la migración

1. Detener la aplicación y comprobar que no existe otra ejecución cooperativa usando la instalación.
2. Comprobar que las cuentas y la carpeta CSV pertenecen a la misma instalación. Obtener los identificadores de las cuentas de la base, no de datos facilitados por el navegador.
3. Rechazar carreras activas, cierres pendientes o acciones que necesiten revisión. Validar cada fila, su propietario y los cierres confirmados antes de importar.
4. Importar los historiales en una sola transacción de la base de destino. Un conflicto debe cancelar la operación; los reintentos no deben duplicar carreras.
5. Comparar el resultado con el origen y conservar los CSV. Los historiales antiguos sin propietario no se asignarán automáticamente a ningún conductor.
6. Diseñar y probar por separado cómo cambiar el vínculo de almacenamiento. Importar filas no cambia por sí solo el historial que utiliza la aplicación.

Una copia de los CSV solo está completa si se han identificado todos los archivos que pertenecen a la instalación. Cuando falten comprobantes o no sea posible identificar un archivo, habrá que comunicar la limitación o detener la operación en lugar de inventar datos.

## Alcance y comprobaciones

La primera pieza será experimental y no se conectará automáticamente al arranque. Las pruebas usarán bases y CSV temporales e incluirán varias cuentas, reintentos, duplicados, datos incompatibles, archivos ausentes, carreras pendientes y bloqueos. Los bloqueos coordinan procesos de TaxiRoute que los respeten; no protegen frente a modificaciones externas que los ignoren. El protocolo final de cambio de almacenamiento y las copias para uso real requieren comprobaciones adicionales.

La primera función interna experimental se encuentra en `taxiroute/importacion_flota_csv.py`. Exige un destino ya inicializado y fuera de la carpeta CSV. Informa de las cuentas sin historial, de las filas sin comprobante y de la presencia de historial antiguo sin propietario. Los archivos auxiliares de bloqueo y las carpetas de cuenta pueden crearse durante la comprobación; los contenidos CSV no se modifican. Todavía no hay una orden de migración para personas usuarias ni un procedimiento para activar el nuevo vínculo.
