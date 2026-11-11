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

La primera función interna experimental se encuentra en `taxiroute/importacion_flota_csv.py`. Exige un destino ya inicializado y fuera de la carpeta CSV. Informa de las cuentas sin historial, de las filas sin comprobante y de la presencia de historial antiguo sin propietario. Los archivos auxiliares de bloqueo y las carpetas de cuenta pueden crearse durante la comprobación; los contenidos CSV no se modifican. Todavía no hay una orden de migración para personas usuarias ni un procedimiento listo para datos reales.

## Identidad y activación futura

El historial SQLite puede recibir una identidad aleatoria persistente mediante la función experimental de `taxiroute/identidad_historial_sqlite.py`. La identidad permite distinguir bases creadas por separado y se prepara de forma transaccional sin modificar las carreras. Una copia antigua de la misma base conserva la identidad: por sí sola, esta comprobación no detecta que se haya restaurado una copia desactualizada ni protege contra modificaciones deliberadas.

El cambio de almacenamiento deberá mantener el bloqueo de la instalación durante la comprobación y el cambio de vínculo. Como cuentas e historial son archivos distintos, no se debe presentar la operación como una única transacción entre ambos. Antes de activar SQLite será necesario revisar cualquier historial antiguo sin propietario, cuenta sin CSV o fila sin comprobante, y comprobar los cierres confirmados. La identidad, el cambio de vínculo y la verificación de arranque existen como piezas experimentales y se han probado con instalaciones temporales. Si se interrumpe antes de confirmar el vínculo de cuentas, CSV continúa activo y se puede revisar y reintentar; si la confirmación da un resultado incierto hay que revisar el estado antes de continuar. Los SQLite no forman una transacción conjunta. El asistente local experimental todavía no constituye un procedimiento validado para activar una instalación real. Una vez se escriba en SQLite no se debe volver automáticamente al CSV, que puede haber quedado desactualizado.

En el prototipo, una cuenta sin archivo CSV, incluso una cuenta nueva sin carreras, detiene la activación si no se confirma su revisión, porque no se puede demostrar solo con esa ausencia si se perdió un historial. Las filas sin comprobante y el historial anterior sin propietario también detienen el cambio. Para su uso práctico se necesita una revisión explícita de esas situaciones.

El criterio acordado para una cuenta sin CSV es solicitar que un administrador revise expresamente el caso y que esa decisión quede registrada. El mecanismo interno está implementado y se ha probado con sesiones y cuentas ficticias; sin una confirmación válida, la activación sigue deteniéndose. La confirmación no convierte por sí sola un archivo perdido en un historial vacío. La función interna experimental exige confirmar cada cuenta ausente, verifica una sesión administrativa vigente y registra la cuenta, el actor, el vínculo y la fecha en la misma transacción que el cambio. Comprueba de nuevo las ausencias mientras mantiene el bloqueo. Existe un asistente local experimental de terminal que permite hacer la revisión individual y confirmar el cambio; aún necesita validación adicional y un procedimiento preparado para datos reales.

La activación experimental conserva también la procedencia del CSV en ambas bases y rechaza reutilizar un destino preparado para otra instalación. Esto evita confundir dos instalaciones distintas, pero no detecta por sí solo una copia antigua de la misma instalación ni sustituye a las copias de seguridad.

El prototipo rechaza una base SQLite que ya tenga identidad pero no tenga una procedencia verificable. Una instalación experimental anterior en ese estado necesita revisión; no se le asigna automáticamente un origen.

## Asistente experimental

`python -m taxiroute.asistente_migracion_local --help` muestra las rutas necesarias. El asistente requiere una terminal interactiva, aplicación detenida y autenticación administrativa mediante contraseña y código. Muestra cada cuenta sin CSV y solicita una confirmación individual, seguida de una confirmación final. Si el destino no existe, lo crea sin sobrescribir archivos; si la transición falla puede quedar un destino preparado para revisar o reintentar. La aplicación vuelve a comprobar la lista y los archivos durante la activación. No se debe usar con datos reales hasta validar el procedimiento y disponer de copias.
