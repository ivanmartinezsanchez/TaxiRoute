# TaxiRoute — Acceso protegido y funcionamiento de flota

Fecha de decisión: 7 de noviembre de 2026.

## Estado y propósito

Este documento recoge el alcance aprobado para ampliar la fase 3. Describe requisitos y criterios de aceptación, no funciones ya terminadas. La web actual dispone de un servicio de carreras compartido, sin cuentas individuales. Las fases 1 y 2 conservan sus entregas.

La aplicación admitirá carreras simultáneas en vehículos diferentes. Se ha aprobado como evolución el acceso desde dispositivos situados en ubicaciones distintas, con cuentas independientes y una única sesión válida por cuenta. Su implementación queda aplazada a una entrega posterior, sin fecha fijada. El trabajo inmediato continúa en la versión local; no se habilitará todavía acceso remoto.

### Evolución aprobada y aplazada: flota remota

La primera opción acordada es ejecutar el servicio compartido en el ordenador del administrador y conectar los dispositivos mediante una red privada, con HTTPS. El equipo central y su conexión deberán estar disponibles. La herramienta de conexión, la configuración de certificados y el almacenamiento compartido se concretarán al retomar esta entrega; no se adelanta la base de datos de fase 4 sin revisar la separación de fases.

Se permitirá una única sesión válida por cuenta, controlada por el servidor. Esto limita sesiones de navegador, no acredita por sí solo la identidad física de un dispositivo. El cambio de dispositivo requerirá un procedimiento explícito que invalide la sesión anterior y conserve la carrera activa. La administración podrá revocar sesiones y facilitar la recuperación de acceso.

La primera entrega remota conservará la carrera en el servidor durante una desconexión y mostrará el estado de conexión. No dará por confirmadas órdenes que el servidor no haya recibido. El último estado confirmado seguirá siendo la referencia de cálculo; no se garantizará un importe fiel a cambios de movimiento ocurridos sin comunicación. El registro de operaciones sin conexión y su sincronización posterior quedan para una evolución adicional.

Antes de habilitar el acceso remoto se comprobarán, con dos dispositivos, el aislamiento entre cuentas y vehículos, las tarifas por carrera, la exclusión de sesiones simultáneas de una cuenta, el cambio de dispositivo y las pérdidas de conexión. Esta funcionalidad figura como aprobada y pendiente, no como implementada.

## Cuentas, roles y carreras

El administrador dispondrá de panel administrativo y panel de conductor con una sola cuenta. Podrá realizar carreras, gestionar conductores, consultar la actividad de la flota y modificar tarifas. Los conductores gestionarán sus carreras y consultarán su propio historial y las tarifas aplicables.

La autorización se comprobará en el servicio Python para cada operación. Ocultar botones no constituye un control de acceso. Cada carrera nueva se vinculará a su conductor y vehículo; las carreras anteriores sin atribución no se asignarán arbitrariamente a una cuenta.

La implementación separará el estado, las órdenes, las revisiones y los guardados pendientes de cada carrera. Se comprobará que una sesión no pueda acceder a otra carrera cambiando un identificador. La regla de exclusividad por vehículo y conductor se concretará antes de implementar el registro de vehículos.

## Tarifas

El administrador podrá guardar tarifas nuevas mientras existan carreras en curso, incluida la suya. Cada carrera conservará una copia de las tarifas vigentes en el momento de iniciarse hasta su finalización. Los cambios no recalcularán sus tramos ni su total.

Una carrera que comience después de guardar el cambio utilizará las tarifas nuevas, aunque otro vehículo siga realizando una carrera con tarifas anteriores. La interfaz distinguirá tarifas de la carrera y tarifas para nuevos servicios.

## Contraseñas y acceso administrativo

Las contraseñas tendrán al menos doce caracteres, incluyendo una mayúscula, tres minúsculas, tres números y un carácter especial. Se permitirá pegar contraseñas y mostrar u ocultar su contenido.

Se utilizará Argon2id mediante una biblioteca mantenida, con salt individual gestionado por la biblioteca. Los parámetros se medirán en el equipo de referencia antes de fijarlos. No se almacenarán contraseñas originales ni versiones reversibles. El restablecimiento sustituirá la contraseña; no permitirá consultar la anterior.

Los conductores accederán con contraseña. El administrador deberá completar además una comprobación con un código aleatorio de siete cifras enviado al correo configurado para su cuenta. La dirección real y las credenciales del servicio de envío permanecerán fuera del repositorio.

El código caducará a los cinco minutos, será de un solo uso y estará vinculado al intento de acceso. Un reenvío invalidará el código anterior y exigirá una espera mínima de un minuto. Se limitarán también los envíos y las verificaciones en el servidor. No se registrarán códigos ni contraseñas en los eventos.

El correo necesita conectividad aunque el cálculo se ejecute localmente. Un fallo de entrega no permitirá omitir la comprobación. El proveedor de envío y sus credenciales siguen pendientes de configuración.

### Límite del correo como comprobación

NIST SP 800-63B-4 no admite el correo electrónico como canal de autenticación fuera de banda. Su uso aquí es una decisión del prototipo, con dependencia del buzón, posibles retrasos y exposición a suplantación. No se declara conformidad con los niveles de garantía de NIST. Para una evolución de mayor garantía se evaluará autenticación resistente al phishing con claves criptográficas no exportables y un procedimiento de recuperación equivalente.

## Sesiones, bloqueo y recuperación

Las sesiones serán revocables y se comprobarán en el servidor. Las cookies se protegerán con HttpOnly, SameSite y Secure en HTTPS; las operaciones de cambio de estado mantendrán protección frente a solicitudes cruzadas. La política aprobada establece una hora de inactividad y ocho horas de duración máxima desde la creación de la sesión. Se aplicará el límite que se alcance primero; la interacción no ampliará el máximo absoluto. La implementación sigue pendiente.

El bloqueo de TaxiRoute o de Windows no finalizará una carrera. La actualización automática del contador no contará como interacción del usuario. La suspensión, el apagado y la caída del proceso son situaciones diferentes: la continuidad y recuperación ante ellas no están resueltas por un bloqueo de pantalla.

Se ofrecerá un modo opcional para mantener la pantalla encendida durante una carrera, condicionado a las capacidades del entorno. No se modificará de forma permanente la política de bloqueo del sistema operativo.

La cuenta quedará bloqueada tras quince intentos consecutivos fallidos; en el administrador se contarán también los fallos del PIN. Solo el acceso completo correcto reiniciará el contador. El contador persistirá entre reinicios y se combinará con esperas progresivas. La reactivación ordinaria corresponderá al administrador.

La recuperación del único administrador utilizará contraseña y un código de recuperación de un solo uso generado durante la configuración inicial. Los códigos se guardarán fuera de la aplicación; sus verificadores se protegerán en el almacenamiento. La recuperación se registrará y revocará las sesiones anteriores. El procedimiento para pérdida simultánea de contraseña y códigos deberá definirse sin introducir una puerta de acceso alternativa.

## Administración de cuentas

Se incluirán creación de conductores, usuario único, contraseña temporal con cambio obligatorio, restablecimiento, desactivación, reactivación, desbloqueo y revocación de sesiones. El historial se conservará al desactivar una cuenta.

Se registrarán actor, operación, cuenta afectada y fecha de las acciones administrativas, evitando secretos. El último administrador no podrá eliminarse ni desactivarse. Los cambios sensibles requerirán nueva comprobación de identidad.

Si se desactiva un conductor con carrera activa, la carrera se conservará para su gestión administrativa. No se finalizará ni descartará silenciosamente.

## Selección técnica

| Componente | Decisión | Alternativas y motivo | Coste y mantenimiento | Verificación o revisión |
|---|---|---|---|---|
| Contraseñas | Argon2id | scrypt es alternativa válida; bcrypt se reserva para compatibilidad heredada; PBKDF2 se evaluaría ante exigencias normativas específicas | Biblioteca adicional y ajuste de consumo; sin servicio externo | Medir memoria y tiempo de acceso; revisar parámetros al actualizar |
| Comunicación | HTTPS con certificado de confianza local | HTTP no aporta cifrado; un certificado sin confianza provoca advertencias | Instalación y renovación de certificados y protección de claves | Confirmar topología, nombres y dispositivos antes de emitir certificados |
| Interfaz y lógica | Mantener Flask y separar servicios por carrera | Cambiar de framework no resuelve por sí mismo el aislamiento | Refactorización y pruebas de permisos y concurrencia | Revisar antes de habilitar varios procesos o equipos |
| Cuentas y controles de acceso | SQLite aprobado en fase 3; historial CSV hasta fase 4 | JSON requeriría coordinación manual y migración de cuentas; PostgreSQL añade un servicio innecesario ahora | Sin servidor adicional; requiere transacciones, copias y mantenimiento de esquema | Revisar ante varios servidores o contención medida; diseño en DISENO_CUENTAS.md |

La confianza del certificado no sustituye una configuración TLS adecuada. Para entornos de mayor exigencia se evaluarán TLS moderno, gestión y renovación de certificados, protección de claves, autenticación de dispositivos cuando proceda y recuperación de acceso. Ninguna opción aislada garantiza la seguridad del conjunto.

## Entregas y pruebas de aceptación

1. Concretar almacenamiento y reglas de asignación para la versión local, manteniendo aplazada la conexión remota de vehículos.
2. Separar carreras por conductor y vehículo. Comprobar dos carreras simultáneas sin mezcla de estado, tarifas ni errores de guardado.
3. Incorporar cuentas, Argon2id y permisos. Probar acceso cruzado denegado y contraseñas temporales.
4. Añadir PIN, límites, bloqueo y recuperación. Probar caducidad, reutilización, reenvíos, reinicios y fallos del correo sin envíos reales en las pruebas.
5. Incorporar panel administrativo, revocación y gestión de carreras de cuentas desactivadas.
6. Configurar HTTPS, sesiones y bloqueo de pantalla según el entorno acordado.
7. Verificar interfaces adaptables, teclado, foco, mensajes y regresión de las funciones anteriores.

La aceptación incluirá dos vehículos con tarifas antiguas, un cambio administrativo y una nueva carrera con tarifas nuevas. Las carreras ya iniciadas deberán finalizar con su configuración original. También se comprobará que cerrar sesión de un conductor no cierre el servicio de la flota.

Las pruebas remotas y de sesión única por cuenta pertenecen a la evolución aplazada descrita al principio del documento. Cada entrega se revisará antes de avanzar. El manual final describirá únicamente funciones implementadas y comprobadas.

## Comprobación previa del aislamiento — 7 de noviembre de 2026

Se han añadido tres pruebas en tests/test_servicios_independientes.py. Comprueban servicios con carreras solapadas, cambios de tarifas aplicados a nuevas carreras sin esperar a otro vehículo y un guardado fallido que permanece pendiente sin bloquear el otro servicio. El reintento conserva la carrera y evita duplicarla.

Se han superado las 64 pruebas locales en Windows. Los archivos de estas pruebas son temporales; no se modifica el historial de uso. Esta validación utiliza instancias independientes de ServicioCarreras y acciones secuenciales con tiempos controlados. No acredita peticiones concurrentes, autorización entre cuentas ni aislamiento de la web actual, que todavía utiliza un servicio compartido. Tampoco añade atribución de conductor o vehículo al CSV existente.

## Referencias

- [OWASP: almacenamiento de contraseñas](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
- [OWASP: protección TLS](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html).
- [NIST: autenticación y gestión de autenticadores](https://pages.nist.gov/800-63-4/sp800-63b.html).

## Diseño de cuentas

La elección de SQLite ha sido confirmada. El [diseño de cuentas](DISENO_CUENTAS.md) propone tablas, restricciones y orden de implementación. La inicialización y la tabla usuarios se han implementado el 8 de noviembre y probado en bases temporales. No se ha creado una base de cuentas de uso real ni migrado el historial; la autenticación continúa pendiente.

## Avance del PIN — 8 de noviembre de 2026

El servicio interno de PIN y la migración SQLite a versión 2 están implementados y probados con transporte simulado. La suite local supera 113 pruebas. La conexión con un proveedor de correo, el flujo web y las sesiones aún no están implementados; no se han enviado mensajes reales. Los detalles y límites se recogen en [DISENO_CUENTAS.md](DISENO_CUENTAS.md).

## Avance del cambio administrativo — 8 de noviembre de 2026

Se ha conectado el PIN con el cambio obligatorio de contraseña mediante una autorización temporal de un solo uso almacenada como hash. El esquema alcanza la versión 3; se han superado 124 pruebas locales. La integración web y las sesiones siguen pendientes. No se han enviado correos reales.

## Avance de sesiones — 8 de noviembre de 2026

Se han implementado sesiones internas persistentes con una hora de inactividad y ocho horas máximas. La creación se enlaza al acceso completo y el cierre no modifica carreras. El esquema alcanza la versión 4 y se han superado 137 pruebas locales. La integración web, las cookies, HTTPS y la administración de sesiones siguen pendientes. La sesión única por cuenta continúa aplazada con el acceso remoto.


## Ayuda en acciones del panel administrativo

Al implementar el panel, cada acción dispondrá de una explicación desplegable que se pueda abrir con ratón, teclado y pantalla táctil. No es necesario mostrar la ayuda al pasar el puntero. Los mensajes serán:

- Desactivar: «Impide el acceso y revoca las sesiones del conductor».
- Reactivar: «Habilita la cuenta, pero conserva cualquier bloqueo por intentos».
- Desbloquear: «Borra el bloqueo, los fallos y la espera, pero no habilita una cuenta desactivada».
- Revocar: «Cierra las sesiones del conductor sin desactivar su cuenta».

La ayuda debe ser accesible para lectores de pantalla y no depender solo del color. Se comprobará con ratón, teclado y pantalla táctil cuando se implemente. El panel y estos mensajes todavía no existen en la interfaz.


## Maqueta de administración

Se ha preparado taxiroute/templates/panel_administracion_borrador.html como maqueta aislada con datos ficticios y acciones deshabilitadas. No tiene ruta ni acceso a cuentas y no constituye un panel funcional. Sus controles de ayuda desplegables son un primer diseño accesible para teclado y tacto; queda pendiente comprobarlos en el panel funcional con navegador y lector de pantalla. La integración necesita previamente acceso protegido, permisos y controles de origen.
