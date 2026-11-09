# Recuperación de carreras de TaxiRoute

## Objetivo y estado

Este documento recoge las decisiones para la fase 3 y describe una propuesta de implementación. La recuperación que se explica aquí **todavía no está implementada**. La web habitual aún utiliza una carrera compartida en memoria; las pruebas de flota y acceso están aisladas.

Se ha decidido conservar el historial de carreras finalizadas en CSV durante la fase 3. La base SQLite, ya elegida para cuentas, se ampliará para conservar las carreras activas, los resúmenes pendientes de exportar al historial y la relación entre carrera y conductor. En fase 4 se revisará la migración del historial, conservando una copia del CSV.

Tras una interrupción se recuperará el último estado guardado. No se sumará automáticamente el tiempo en que no se pudo registrar la carrera. La carrera recuperada requerirá revisión antes de finalizarse. Ese intervalo puede incluir tiempo efectivamente transcurrido: el importe recuperado no debe presentarse como una reconstrucción exacta.

## Información necesaria

Cada registro de recuperación deberá incluir, como mínimo:

- Identificador único de carrera e identificador interno del conductor.
- Estado del proceso: activa, requiere revisión tras interrupción, finalizada pendiente de guardar o guardada.
- Fecha de inicio y fecha del último punto de control.
- Estado del vehículo en ese punto, tiempos acumulados por estado y tarifas fijadas al iniciar la carrera.
- Revisión de la carrera y, una vez finalizada, los datos completos del resumen que se debe guardar.
- Resultado de la revisión de una recuperación, con fecha y responsable, cuando se diseñe esa interacción.

Los importes y tarifas se conservarán con representación decimal exacta. La identidad del conductor procederá de una sesión validada; no se aceptará que el navegador decida a quién pertenece la carrera. Las reglas y el identificador de vehículo se definirán al incorporar vehículos.

## Puntos de control y recuperación

Se propone guardar un punto de control al iniciar, cambiar de estado y finalizar, así como de forma periódica durante una carrera activa. Se ha acordado una frecuencia inicial de cinco segundos. El coste y el máximo de tiempo que podría quedar sin registrar deben probarse antes de afirmar una garantía concreta. Si falla un punto de control, la interfaz deberá advertirlo; no se dará a entender que la recuperación está asegurada.

Al arrancar después de una interrupción se comprobarán los registros incompletos. Los datos se recuperarán hasta el último punto válido y se mostrará que necesitan revisión. No se sumará por defecto el intervalo posterior al último punto válido. La forma de revisar y confirmar la carrera debe concretarse en la interfaz antes de habilitar la finalización. No se reutilizará sin validación un valor del reloj monotónico almacenado antes de un reinicio.

Las carreras finalizadas pendientes conservarán su resumen y su identificador para permitir un nuevo intento de guardado. Si los datos están incompletos, son contradictorios o no se pueden leer, se conservarán para revisión y se mostrará el problema, sin repararlos ni borrarlos automáticamente.

## Coordinación con el historial CSV

SQLite y CSV no comparten una única transacción. Por tanto, no se puede prometer que ambas escrituras se completen o se deshagan a la vez. La propuesta es:

1. Confirmar en SQLite el resumen finalizado como pendiente, antes de intentar escribirlo en el CSV.
2. Escribir el resumen en el CSV usando el identificador de carrera y el bloqueo de historial existente.
3. Después de comprobar que el resumen está presente y coincide, marcarlo como guardado en SQLite. Mantener la relación carrera-conductor para poder filtrar el historial sin modificar las filas antiguas.
4. Tras una interrupción, revisar los pendientes: si la fila completa ya existe y coincide, completar la confirmación; si no existe, ofrecer o realizar un reintento controlado; si existe con datos distintos o el archivo está incompleto, detener el proceso y avisar.

El historial actual ya evita volver a añadir un identificador con datos idénticos y rechaza uno coincidente con datos diferentes. Esa comprobación es útil para el reintento, pero por sí sola no prueba durabilidad frente a apagones, garantiza que una escritura haya llegado al disco ni repara una fila parcial. Habrá que diseñar y probar la sincronización y recuperación para cada punto de fallo.

Las carreras anteriores a las cuentas no tienen una relación fiable con un conductor. Se conservarán sin asignarlas automáticamente. El acceso al historial deberá distinguirlas de las nuevas; su presentación y las funciones disponibles para el administrador se definirán antes de integrar la pantalla.

## Seguridad, copias y fallos

Los datos estarán fuera del repositorio. Se limitará el acceso con los permisos del sistema y se incluirán la base y el historial en el procedimiento de copia de seguridad. Una copia coherente debe tener en cuenta ambos archivos y las escrituras en curso; copiar solo uno puede dejar una relación incompleta.

Antes de utilizar datos reales se probarán falta de espacio, permisos, base ocupada, cierre durante cada paso del guardado, CSV incompleto, coincidencia y conflicto de identificadores, reinicio repetido y dos conductores simultáneos. También se comprobará que una persona no pueda recuperar ni consultar la carrera de otra cambiando datos enviados por el navegador. Se mantendrán las pruebas anteriores de fases 1 y 2.

## Pendiente antes de implementarlo

- Implementar los puntos de control cada cinco segundos y concretar cómo informar de una escritura fallida.
- Diseñar la pantalla y el procedimiento de revisión después de una interrupción.
- Definir la migración versionada de la base y el tratamiento de copias de seguridad.
- Acordar el comportamiento y los permisos sobre las carreras históricas sin conductor.
- Probar el protocolo SQLite–CSV y sus fallos antes de usar datos reales.
## Estructura preparada — 8 de noviembre de 2026

La versión 6 del esquema añade `carreras_recuperacion` mediante una migración transaccional. Incluye la cuenta, el estado, las fechas, los tiempos acumulados, las tarifas, el importe final y la revisión. Una restricción impide más de una carrera activa, en revisión o pendiente de guardar por usuario. Las carreras ya guardadas pueden conservarse para relacionarlas con su conductor.

Las pruebas con bases temporales comprueban la migración desde la versión 5, la conservación de cuentas, la reversión si falla y algunas restricciones. La estructura no implementa todavía validación completa de fechas ni de importes y tarifas; tampoco escribe puntos de control, recupera carreras, coordina el CSV ni presenta una interfaz. No se ha migrado una base de uso real en estas pruebas.
## Lectura y escritura experimental de puntos — 8 de noviembre de 2026

`taxiroute/puntos_control.py` incorpora una estructura inmutable `PuntoControl` y funciones internas para guardar y leer puntos de carreras activas en SQLite. La escritura usa una transacción y valida identificadores, fechas con zona horaria, estado, tiempos, revisión y el formato de tarifas. Al actualizar, rechaza un control que no avance en fecha, reduzca los tiempos o la revisión, cambie la cuenta, el inicio o las tarifas, o cambie de estado sin avanzar la revisión. Dos controles con la misma fecha no se aceptan como actualizaciones sucesivas.

Las pruebas utilizan bases temporales y cubren lectura por ambos identificadores, datos inválidos, escrituras simultáneas y un fallo de actualización simulado. Estas funciones no autentican al llamador ni están conectadas a la web, a un temporizador o al cálculo de una carrera. Tampoco reconstruyen carreras, gestionan pendientes o coordinan el CSV. La validación actual no demuestra coherencia completa entre tiempo, fecha e importe. Un ajuste hacia atrás del reloj de pared puede hacer que se rechacen nuevos controles y requiere tratamiento antes de la integración. No se ha probado recuperación tras una caída real ni se garantiza durabilidad frente a apagones.

## Captura de una carrera activa — 8 de noviembre de 2026

`Carrera.capturar()` obtiene una copia inmutable de la carrera activa. Lee el reloj monotónico una vez, suma el tramo actual a una copia de los tiempos acumulados y recoge fecha, estado y tarifas sin detener o modificar la carrera. Rechaza una carrera finalizada y un reloj monotónico que retrocede. `punto_de_captura()` convierte esos datos en un punto de control validado con la cuenta y la revisión suministradas por el llamador; representa las tarifas como decimales sin notación exponencial.

Las pruebas usan relojes controlados y una base temporal para comprobar tramos, continuidad del cálculo y lectura del punto guardado. La captura no incorpora un bloqueo propio: quien la integre con otras operaciones deberá coordinar captura, cambio de estado y finalización con el mismo bloqueo. La identidad y la revisión deben proceder de fuentes verificadas. No se ha activado el guardado periódico ni se reconstruye una carrera; los relojes de pared, los fallos de escritura y la recuperación siguen pendientes.

## Captura coordinada en la flota experimental — 8 de noviembre de 2026

`FlotaEnMemoria.capturar()` toma una captura de la carrera activa y su revisión bajo el mismo bloqueo que utiliza para las acciones de la flota. No crea un servicio cuando no existe ni devuelve una captura si la carrera ya no está activa. Una prueba con dos hilos fuerza que un cambio de estado espere mientras se captura y comprueba que cada resultado conserva su revisión. Otras pruebas comprueban dos usuarios y la finalización.

La coordinación afecta únicamente a operaciones realizadas a través de esa instancia de `FlotaEnMemoria` en un mismo proceso. Un código que modifique directamente un servicio compartido, otra instancia o proceso no queda protegido por este bloqueo. La captura no se guarda todavía ni hay temporizador, autorización adicional, recuperación o coordinación con CSV. Al conectar el guardado habrá que evitar que una captura anterior sobrescriba otra posterior y tratar explícitamente los errores y el retroceso del reloj de pared.

## Guardado coordinado experimental — 8 de noviembre de 2026

`FlotaEnMemoria.guardar_captura()` abre primero una transacción SQLite de escritura y después adquiere el bloqueo de la flota; conserva este último durante captura y commit. La ruta experimental de acciones autenticadas utiliza el mismo orden: SQLite antes de flota. El método devuelve el punto guardado o `None` si no existe carrera activa, y propaga los errores de validación o de SQLite. No autentica a su llamador y no debe invocarse dentro de otra transacción de escritura sobre la misma base.

Las pruebas con bases temporales cubren usuarios separados, actualización de un punto, rechazo de una captura antigua, fallo simulado sin pérdida del punto anterior y una acción autenticada simultánea con el guardado. Son escenarios acotados, no una demostración de ausencia total de bloqueos ni de durabilidad frente a apagones. La operación mantiene bloqueos durante la escritura y puede esperar o fallar si SQLite está ocupado.

No se ha conectado a un temporizador ni a la aplicación de uso. La finalización durable aún no está coordinada: un punto previamente guardado puede seguir apareciendo como activo tras finalizar la carrera en memoria. Por tanto, no debe activarse la recuperación a partir de estos puntos hasta diseñar y probar la transición de finalización, la coordinación con CSV y la recuperación de fallos. Si el reloj de pared retrocede o se repite, la actualización puede rechazarse; la interfaz todavía no avisa de esos fallos.

## Transición experimental a resumen pendiente — 8 de noviembre de 2026

`finalizacion_pendiente.py` permite marcar en SQLite como `guardado_pendiente` un punto activo que ya exista y coincida exactamente con el último guardado. El instante de finalización debe coincidir con el de esa captura. La operación comprueba la cuenta, conserva los datos del punto y calcula el total a partir de tiempos en nanosegundos y tarifas decimales, con redondeo a céntimos al final. Un reintento idéntico mientras el resumen sigue pendiente devuelve el mismo resultado; una captura distinta o una carrera en revisión se rechaza.

Las pruebas usan cuentas y bases temporales y cubren reintentos simultáneos, errores de actualización, datos que no coinciden y redondeo. Esta función no autentica, no finaliza el objeto de carrera en memoria y no escribe ni confirma el CSV. Exige que el último punto ya se haya guardado: todavía falta diseñar y probar la creación del resumen si no existe punto previo y cómo coordinar captura, memoria, SQLite y recuperación sin perder cambios. La carrera pendiente continúa impidiendo iniciar otra para esa cuenta en el esquema. La transición no resuelve todavía la limitación observada en el guardado de flota.

La revisión independiente detectó además que una operación SQL podía no actualizar ninguna fila. La transición comprueba ahora que se haya actualizado exactamente una y devuelve error en caso contrario. El resultado `ResumenPendiente` aún no es el objeto `ResumenCarrera` que espera `HistorialCSV`: la conversión y la comprobación de igualdad del resultado se deberán diseñar y probar antes de escribir el CSV. La coincidencia con el último punto guardado no demuestra que dicho punto sea la captura del momento real de finalización.

## Conversión experimental para el historial — 8 de noviembre de 2026

`resumen_para_historial()` convierte un `ResumenPendiente` al tipo `ResumenCarrera` que recibe el historial CSV. Comprueba el punto, la fecha y que el importe coincida con el cálculo sin redondeos intermedios. Los tiempos en nanosegundos se transforman a segundos decimales. Una prueba con reloj controlado compara el resultado con el resumen normal de una carrera y comprueba un reintento de guardado en un CSV temporal.

La conversión no lee ni valida por sí sola la procedencia del resumen en SQLite, no autentica ni coordina el CSV con la base. No finaliza la carrera en memoria. La revisión independiente recomienda preparar el punto final y la transición a pendiente en una misma transacción SQLite, con el orden de bloqueos SQLite → flota, antes de sincronizar la memoria. Una interrupción después del commit o un resultado de commit incierto requieren reconciliación; no se ha implementado. Tampoco se ha demostrado igualdad de los cálculos en todos los valores extremos admitidos.

## Preparación transaccional del punto final — 8 de noviembre de 2026

La preparación experimental puede insertar o actualizar la captura final y marcarla como pendiente dentro de una misma transacción SQLite, incluso si no había un punto previo. Una captura idéntica puede reutilizarse; se rechazan los puntos antiguos, incoherentes o de otro usuario. El llamador que aporta una conexión debe haber iniciado una transacción de escritura y es responsable de confirmar o revertir el conjunto. Un `SAVEPOINT` interno revierte los cambios propios de la preparación si esta falla, sin confirmar ni cancelar los demás cambios de la transacción del llamador. El wrapper independiente abre y confirma su propia transacción.

Las pruebas con bases temporales cubren fallo del segundo paso después de insertar o actualizar, escrituras ignoradas, reintentos y finalizaciones concurrentes. No se ha conectado a la carrera en memoria, la sesión web ni el CSV. Una transacción SQLite no abarca esos componentes: antes de la integración se debe diseñar la sincronización y reconciliación tras una caída o un resultado de commit incierto. La API actual de sesión no entrega su conexión al callback; abrir otra transacción de escritura en ese callback puede bloquearse. El módulo no autentica al llamador ni demuestra igualdad de cálculo en todos los valores extremos.

## Preparación experimental con sesión — 8 de noviembre de 2026

El servicio de sesiones puede ejecutar una operación interna con la identidad validada y la misma conexión SQLite, dentro de una transacción de escritura. `preparar_punto_final_con_sesion()` utiliza esa conexión para preparar un resumen y exige que la cuenta de la sesión coincida con la del punto. Las pruebas con cuentas temporales cubren sesión válida, otra cuenta, sesión revocada o caducada, fallo SQL y cierre concurrente. No se ha habilitado una ruta web para esta función.

El punto debe proceder de una captura interna coordinada; la función no verifica que sea la captura actual de la carrera en memoria y no debe recibir datos del navegador. La captura y las acciones de la flota todavía no están coordinadas con esta operación hasta confirmar SQLite. No se modifican la carrera en memoria ni el CSV y no existe reconciliación ante caída o commit incierto. La operación que recibe la conexión no debe confirmarla, revertirla ni abrir otra escritura de la misma base; esas reglas son un contrato interno, no una restricción impuesta al callback por SQLite. La integración real queda pendiente.

## Cierre coordinado experimental y bloqueo preventivo — 8 de noviembre de 2026

`FlotaEnMemoria.preparar_cierre_experimental()` valida una sesión en SQLite, obtiene el bloqueo de flota, comprueba la revisión y captura la carrera una sola vez. Prepara el punto final y el resumen pendiente en esa misma transacción. Antes de intentar el commit marca el usuario como pendiente de reconciliación y conserva el bloqueo de flota hasta que el commit devuelve o falla. Si la preparación falla antes del commit se revierte sin marcar ese bloqueo; si el commit devuelve éxito o lanza un error, se mantiene el bloqueo en memoria. Las consultas y mutaciones de esa carrera informan del bloqueo; los demás usuarios pueden continuar.

Las pruebas con bases temporales comprueban sesión, revisión, usuarios independientes, errores SQL anteriores al commit, fallos simulados antes y después de que el commit se confirme, y una acción concurrente. Una excepción no prueba si el commit se produjo; intentar rollback tampoco permite suponer que no ocurrió. Las pruebas no simulan un apagón real ni demuestran durabilidad del sistema.

Esto sigue siendo una preparación aislada: no está conectada a la web, no finaliza el objeto de carrera en memoria ni escribe el CSV. El bloqueo reside solo en esa instancia y desaparece al reiniciar; todavía no se ha implementado la reconciliación leyendo SQLite ni la exclusión entre instancias o procesos. Antes de utilizar esta vía con datos reales hay que resolver esos casos y comprobar la igualdad del cálculo en límites de precisión. No usar datos enviados por el navegador como identidad o captura.

## Arranque experimental con revisión de pendientes — 8 de noviembre de 2026

`crear_flota_con_recuperacion()` abre una base SQLite existente en modo de solo lectura y comprueba su versión e identidad. Revisa las carreras de todos los estados y valida los puntos y, cuando existen, los resúmenes finales. Si falta la base, el esquema es incompatible, un propietario no existe, los datos no son válidos o una cuenta tiene varias carreras sin resolver, no devuelve una flota. Las cuentas con carreras activas, pendientes de revisión o pendientes de guardar se bloquean en la nueva instancia; no se suma tiempo ni se modifica SQLite al arrancar. Una cuenta cuya carrera figura como guardada no se bloquea por esa fila.

Las pruebas con bases temporales cubren los estados, errores, duplicados y un reinicio simulado después de fallos de commit antes y después de su confirmación. Esta carga no verifica que una carrera marcada como guardada exista y coincida en el CSV ni acredita la integridad total de la base. El bloqueo solo afecta a la instancia creada: otra instancia o una escritura posterior podrían dejarlo desactualizado. No se ha conectado a la web, no reconstruye carreras ni permite resolverlas o desbloquearlas. Debe mantenerse fuera del uso real hasta coordinar esas operaciones y la comprobación del historial.

La conversión al formato del historial normaliza el importe calculado a dos decimales para que representaciones equivalentes no provoquen un conflicto de texto al reintentar el CSV. Se ha probado con un archivo temporal; aún no se ha integrado el protocolo de confirmación entre SQLite y CSV.

## Decisión confirmada — 9 de noviembre de 2026

Tras una interrupción, el intervalo desde el último punto fiable se presentará para revisión y no se cobrará automáticamente. Se conservarán el último punto, sus tarifas y la información necesaria para explicar que el intervalo es desconocido. La decisión y la interfaz concreta de revisión deberán quedar registradas antes de activar la recuperación en la aplicación.

## Conciliación experimental de historiales — 9 de noviembre de 2026

Se han preparado componentes internos para comprobar y guardar carreras pendientes en CSV o en un historial SQLite separado. Los dos archivos no forman una única transacción: si una interrupción ocurre después de escribir el historial y antes de confirmar la recuperación, el reintento verifica los datos y evita duplicar la carrera. En SQLite se coordinan los bloqueos y se comprueban la cuenta y todos los campos. Se han probado concurrencia, errores simulados y reintentos con datos temporales.

Estos componentes aún no están conectados a la web. No permiten por sí solos reconstruir una carrera interrumpida, garantizar el guardado cada cinco segundos ni proteger archivos que se sustituyan fuera de la aplicación.
