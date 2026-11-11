# Diseño del almacenamiento de cuentas

Fecha: 7 de noviembre de 2026.

## Decisión y estado

Se ha aprobado SQLite para cuentas y controles de acceso en la fase 3. El historial de carreras permanece en CSV hasta su migración en la fase 4. El módulo base_cuentas.py implementa el esquema versión 4, con usuarios, configuración administrativa, desafíos de PIN, autorizaciones de cambio y sesiones; conserva las cuentas al migrar desde la versión 1. Solo se han creado bases temporales de prueba: no existe todavía una base de cuentas de uso real ni autenticación.

Se utilizará el módulo sqlite3 de Python. JSON exigiría implementar coordinación y restricciones manuales; PostgreSQL añadiría un servicio que no se necesita para esta entrega local. SQLite requiere gestionar transacciones, contención y copias coherentes, pero no un servidor de base de datos independiente. Se reconsiderará si se necesitan varios servidores o si las mediciones muestran contención relevante.

La ubicación propuesta es ~/.taxiroute/cuentas.sqlite3, fuera del repositorio. Las pruebas utilizarán bases temporales. El archivo no está cifrado por SQLite de forma predeterminada: debe protegerse con permisos del sistema operativo. Su copia de seguridad contiene información sensible aunque las contraseñas estén protegidas.

## Tablas propuestas

### usuarios

Una fila representa una cuenta, no una sesión ni un vehículo.

| Campo | Contenido y regla |
|---|---|
| id | Identificador estable, clave primaria; no cambia al renombrar la cuenta |
| nombre_usuario | Nombre visible de acceso |
| nombre_usuario_normalizado | Valor normalizado y único para impedir duplicados; la política de caracteres se concretará antes de implementarlo |
| nombre_visible | Nombre mostrado en la interfaz |
| rol | administrador o conductor, con restricción CHECK |
| hash_contrasena | Resultado completo de Argon2id, con salt y parámetros; nunca la contraseña original |
| habilitado | Indica si administración permite el acceso |
| bloqueado_en | Fecha UTC de bloqueo por intentos; NULL si no existe ese bloqueo |
| intentos_fallidos | Entero no negativo; bloqueo al alcanzar quince |
| proximo_intento_en | Fecha UTC hasta la que se aplica la espera progresiva |
| cambio_obligatorio | Indica contraseña temporal pendiente de cambio |
| version_credenciales | Contador para invalidar sesiones y desafíos tras cambios sensibles |
| creado_en / actualizado_en / ultimo_acceso_en | Fechas UTC; último acceso solo después de completar la autenticación |

Desactivación administrativa y bloqueo por intentos son condiciones independientes: desbloquear no debe reactivar una cuenta deshabilitada. Un administrador puede conducir por sus permisos; no se crearán dos cuentas para la misma función.

La cuenta no se eliminará físicamente desde el panel. Las comprobaciones del último administrador se realizarán dentro de una transacción, evitando que dos operaciones simultáneas dejen la instalación sin administración.

### configuracion_administrador

Guarda la configuración privada adicional de una cuenta administradora.

| Campo | Contenido y regla |
|---|---|
| usuario_id | Clave primaria y referencia a usuarios |
| correo_verificacion | Destino del PIN; no se publicará en el repositorio |

El servicio comprobará que la cuenta tiene rol administrador. Las credenciales del proveedor de correo y las claves secretas no se guardarán en esta tabla ni en el código.

### desafios_acceso

Representa un intento de autenticación administrativa todavía incompleto. No concede permisos de una sesión autenticada.

| Campo | Contenido y regla |
|---|---|
| id | Identificador aleatorio del desafío |
| usuario_id | Cuenta administradora asociada |
| verificador_pin | Verificador con clave secreta del servidor; nunca el PIN original |
| version_credenciales | Versión para rechazar desafíos anteriores a un cambio sensible |
| creado_en / caduca_en | Fechas UTC; duración aprobada de cinco minutos |
| consumido_en / invalidado_en | Fechas que impiden reutilización |
| fallos | Contador del desafío, adicional al contador persistente de la cuenta |
| estado_envio | Pendiente, enviado o fallido; los estados no válidos no abren sesión |

Un PIN corto no se protegerá solo con un hash rápido sin clave: su espacio de valores es pequeño. Se diseñará un verificador HMAC vinculado al desafío y una clave independiente, con comparación segura. La clave se mantendrá fuera de la base y del repositorio.

Se verificará y consumirá el desafío dentro de una operación atómica. El reenvío invalidará el anterior y no reiniciará los fallos de la cuenta. Se conservará información suficiente para aplicar los límites entre desafíos, incluso al reiniciar. El envío de correo se hará fuera de una transacción de escritura prolongada; se probarán fallo de envío y reintentos.

### codigos_recuperacion

Una cuenta puede tener varios códigos de recuperación de un solo uso.

| Campo | Contenido y regla |
|---|---|
| id | Identificador del registro |
| usuario_id | Cuenta asociada |
| verificador | Representación protegida de un código aleatorio de alta entropía |
| creado_en / usado_en / invalidado_en | Control de vigencia y consumo |

Los códigos originales solo se entregarán en su generación. Su consumo será atómico. La recuperación aprobada exige también contraseña, revoca sesiones y deja registro. La pérdida simultánea de contraseña y códigos requiere un procedimiento todavía pendiente.

### sesiones

Se diseñará como parte de la autenticación local; la restricción de sesión única y el cambio de dispositivo siguen aplazados junto con la evolución remota.

| Campo | Contenido y regla |
|---|---|
| id | Identificador interno de sesión |
| usuario_id | Cuenta autenticada |
| hash_token | Verificador de un token aleatorio de alta entropía; el token original se entrega en la cookie |
| version_credenciales | Permite invalidación tras cambios de contraseña o recuperación |
| creado_en / ultima_interaccion_en / caduca_en | Fechas UTC para duración y caducidad |
| revocada_en | Fecha de revocación; NULL si no se ha revocado |

El token se renovará después de autenticar. No se aceptará como identidad un usuario enviado libremente por el navegador. La actividad automática del contador no renovará la inactividad humana.

## Integridad y acceso

Las referencias usuario_id serán claves foráneas, activadas en cada conexión SQLite. Se utilizarán consultas parametrizadas. Cada operación abrirá o utilizará una conexión con ciclo de vida definido; no se compartirá una conexión global sin coordinación entre peticiones.

Los cambios relacionados se confirmarán juntos mediante transacciones breves: contar fallos y bloquear, consumir un código y completar recuperación, o cambiar credenciales e invalidar accesos. No se mantendrá un bloqueo de escritura mientras se espera al correo ni durante cálculos costosos de contraseña. Se volverá a comprobar la versión de credenciales al confirmar el resultado.

El registro administrativo continuará en el sistema de actividad, sin secretos. Su coordinación con la base y los avisos ante fallos se diseñarán explícitamente: escribir la base y un archivo de registro no constituye una única transacción.

Las actualizaciones del esquema estarán versionadas. No se borrarán tablas para actualizarlas. La base tendrá un procedimiento de copia consistente y una prueba de restauración antes de considerarse terminada esta parte.

## Orden de implementación propuesto

1. Crear el esquema versionado, las conexiones y pruebas de restricciones en una base temporal.
2. Incorporar operaciones de cuentas y protección de contraseñas.
3. Integrar autenticación local, permisos y bloqueos.
4. Incorporar correo, recuperación y sesiones con sus pruebas.
5. Revisar panel administrativo e integración con las carreras.

Las tablas se incorporarán con su funcionalidad y validación correspondiente. El diseño no autoriza activar acceso remoto, enviar correos reales ni crear una contraseña administrativa predeterminada.

## Comprobaciones previstas

- Rechazo de nombres normalizados duplicados, roles inválidos y referencias inexistentes.
- Persistencia de bloqueo y contadores después de reabrir la base.
- Intentos simultáneos sin pérdida de incrementos.
- Desbloqueo sin reactivar cuentas deshabilitadas.
- No reutilización de códigos y desafíos; fallo de correo sin acceso.
- Revocación efectiva después de cambiar credenciales.
- Creación y actualización del esquema sin pérdida de datos.
- Conservación del historial CSV y ausencia de cambios en los datos reales durante pruebas.

## Referencias

- [sqlite3 en Python 3.13](https://docs.python.org/3.13/library/sqlite3.html).
- [Criterios de uso de SQLite](https://www.sqlite.org/whentouse.html).

## Primera implementación — 8 de noviembre de 2026

Se ha añadido taxiroute/base_cuentas.py con conexiones independientes, cierre garantizado, claves foráneas activadas y una tabla usuarios STRICT. Las restricciones rechazan roles no previstos, nombres normalizados duplicados, indicadores inválidos y contadores negativos.

La inicialización utiliza BEGIN IMMEDIATE, application_id y user_version. La creación de la tabla y la versión se confirman juntas; ante un error se revierte la transacción. Se rechazan bases ajenas sin versión y versiones futuras. La versión 1 comprueba la presencia de usuarios, pero no realiza una auditoría completa de alteraciones manuales del esquema.

Las siete pruebas de tests/test_base_cuentas.py utilizan archivos temporales y cubren creación, persistencia, restricciones, versión futura, base ajena, error de creación e inicialización concurrente. Se han superado las 71 pruebas locales en Windows. No se han ejecutado estas nuevas pruebas en GitHub Actions.

Quedan pendientes las operaciones de cuentas, la normalización de nombres, Argon2id, el bloqueo automático al llegar a quince, la gestión del último administrador y el resto de tablas. El hash ficticio de las pruebas solo comprueba almacenamiento SQL; no es una credencial utilizable. Las fechas y la política de contraseñas se validarán en el servicio de cuentas. La tabla no sustituye esa validación.

La base no se crea al importar el módulo ni al abrir la web. El llamador debe proporcionar una ruta en una carpeta existente. No se ha modificado el CSV, creado cuentas reales ni habilitado acceso remoto.

## Alta interna de cuentas — 8 de noviembre de 2026

Se ha añadido taxiroute/cuentas.py con validación, normalización de nombres y alta mediante consultas parametrizadas. El módulo no está expuesto en la web: la autorización administrativa y la configuración inicial siguen pendientes. La operación interna admite ambos roles; no debe conectarse directamente a una petición que permita al visitante elegir sus permisos.

Los nombres de acceso admiten entre 3 y 40 caracteres, letras y números Unicode, punto, guion y guion bajo. Se normalizan con NFC y casefold para comparar, conservando la grafía normalizada para mostrar. No se eliminan tildes ni se resuelven caracteres visualmente parecidos de alfabetos diferentes. El nombre visible admite entre 1 y 100 caracteres sin controles.

Las contraseñas tienen entre 12 y 128 caracteres, una mayúscula, tres minúsculas, tres dígitos ASCII y un signo de puntuación o símbolo Unicode. Se permiten espacios sin que cuenten como símbolo; no se recortan ni normalizan contraseñas. Se rechazan caracteres Unicode de categoría C. La comprobación de contraseñas comunes o filtradas no está implementada.

Se utiliza argon2-cffi 25.1.0, declarada en requirements.txt, con Argon2id, 65536 KiB de memoria, tres iteraciones, paralelismo cuatro, salt de 16 bytes y resultado de 32 bytes. La biblioteca genera un salt aleatorio para cada alta. El cálculo ocurre antes de adquirir el bloqueo SQLite. Los parámetros coinciden con el perfil de la API consultada y se fijan explícitamente.

La mediana local de cinco operaciones de generación más verificación fue de 79.0 ms. Es una medición orientativa en este equipo, no una prueba de carga ni una medición de memoria máxima del proceso. Se revisará ante otras máquinas o accesos concurrentes.

Cada alta genera UUID, fechas UTC y cambio obligatorio de contraseña. CuentaCreada es un resultado inmutable sin contraseña ni hash. Un duplicado produce un error de negocio; otros fallos se conservan para diagnóstico sin disfrazarlos de duplicados. No existe todavía inicio de sesión, PIN, alta desde panel ni una cuenta real.

Las ocho pruebas nuevas comprueban persistencia, verificación real con Argon2id, rechazo de contraseña incorrecta, salts distintos, duplicados por mayúsculas y Unicode, datos inválidos, política de contraseña, conservación de espacios, fallo del hash y alta concurrente. Se han superado las 79 pruebas locales en Windows. Los archivos son temporales y las contraseñas son ficticias. La comprobación en GitHub Actions sigue pendiente.

Referencia: https://argon2-cffi.readthedocs.io/en/stable/api.html

## Comprobación interna de credenciales — 8 de noviembre de 2026

taxiroute/acceso.py incorpora comprobar_credenciales y un resultado tipado sin secretos. La contraseña correcta de un administrador devuelve PIN pendiente; la de un conductor con contraseña temporal devuelve cambio pendiente. Una contraseña ordinaria correcta devuelve contraseña verificada. Ninguno de esos resultados es una sesión ni autoriza una petición.

Los fallos de contraseña admitidos incrementan el contador persistente dentro de una transacción. Se aplican esperas de 1, 2, 4, 8, 16, 32 y hasta 60 segundos; las solicitudes durante la espera no se verifican contra el hash real ni incrementan el contador. Dos intentos ya admitidos que verifican simultáneamente sí contabilizan ambos fallos. Al alcanzar quince, la cuenta queda bloqueada sin desbloqueo automático.

El cálculo Argon2 ocurre fuera de la transacción de escritura. Antes de aceptar el resultado se comprueban de nuevo habilitación, bloqueo, hash, rol y versión de credenciales. Los usuarios inexistentes y las cuentas no disponibles reciben el mismo resultado denegado y se usa un hash ficticio para reducir diferencias evidentes de trabajo. Esto no garantiza tiempos idénticos ni sustituye un límite global de peticiones.

Una credencial almacenada corrupta deniega el acceso; el diagnóstico operativo de ese caso queda pendiente. Las entradas de contraseña no textuales, de más de 128 caracteres o no codificables se rechazan sin verificar. Los fallos de almacenamiento no se convierten en accesos permitidos.

No se reinician contadores ni se registra ultimo_acceso_en todavía: se hará al completar el flujo de acceso, con PIN o cambio de contraseña cuando correspondan. Faltan integración web, sesiones, cambio de contraseña, fallos de PIN, reactivación administrativa, recuperación y auditoría de accesos. El resultado interno no debe aceptarse desde el navegador como prueba de identidad.

Las nueve pruebas nuevas cubren los pasos pendientes, quince fallos, persistencia, espera, cuentas deshabilitadas e inexistentes, hash corrupto, dos fallos simultáneos, cambios de credenciales durante la comprobación y entradas inválidas. Se han superado las 88 pruebas locales en Windows; no se han ejecutado todavía en GitHub Actions. No se han creado cuentas reales ni enviado correos.

## Cambio interno de contraseña — 8 de noviembre de 2026

Se ha incorporado cambiar_contrasena en taxiroute/acceso.py para conductores. Reutiliza comprobar_credenciales: una contraseña actual incorrecta incrementa fallos y respeta esperas y bloqueos. Después valida la nueva, rechaza repetir la actual y genera Argon2id fuera de la transacción de escritura.

Antes de guardar se comprueban de nuevo rol, habilitación, bloqueo, espera y versión de credenciales. El guardado sustituye el hash, desmarca cambio_obligatorio e incrementa version_credenciales en una sola transacción. No borra fallos ni crea sesión ni actualiza el último acceso completo. Las futuras sesiones y desafíos deberán comprobar esa versión; todavía no existe revocación de sesiones implementada.

Los administradores no pueden utilizar esta operación mientras no exista una prueba de PIN verificada por el servidor. No se acepta un booleano del cliente que permita omitirla. La función sigue siendo interna y no está expuesta en la web.

Nueve pruebas nuevas comprueban sustitución de contraseña, fallos de la clave actual, nuevas claves inválidas o iguales, bloqueo y desactivación, espera, rechazo del administrador sin PIN, conservación del contador, fallo de generación, desactivación durante el cálculo y cambios simultáneos. Solo un cambio concurrente sobre la misma versión puede confirmarse.

Se han superado las 97 pruebas locales en Windows. Las cuentas y contraseñas usadas son ficticias y las bases son temporales. Siguen pendientes la interfaz, las sesiones, el PIN y los eventos de auditoría de estas operaciones. No se han enviado correos ni modificado cuentas reales.

## PIN administrativo y esquema versión 2 — 8 de noviembre de 2026

Se han incorporado configuracion_administrador y desafios_acceso mediante una migración transaccional de la versión 1 a la 2. La actualización conserva usuarios y credenciales; si falla, se revierten tanto las tablas nuevas como el número de versión. La inicialización comprueba la presencia de las tablas requeridas, sin pretender auditar todas las alteraciones manuales del esquema.

ServicioPIN, en taxiroute/pin.py, recibe una ruta, una clave HMAC de al menos 32 bytes, un transporte y un reloj. No tiene transporte de correo predeterminado ni genera una clave de producción. La clave deberá generarse aleatoriamente, conservarse fuera de la base y del repositorio y mantenerse entre reinicios. Una clave diferente impide verificar códigos anteriores; su rotación y la configuración privada quedan pendientes. La comprobación de longitud no garantiza por sí sola la entropía de una clave.

solicitar vuelve a comprobar la contraseña y exige rol administrador, cuenta disponible y correo configurado en el servidor. Genera un número entre 1000000 y 9999999 mediante secrets. Guarda un HMAC-SHA256 que vincula código, desafío, usuario, versión de credenciales y correo; el PIN original se entrega únicamente al transporte. No se imprime ni se incluye en el resultado.

Se reserva el envío en una transacción, invalidando los códigos anteriores no consumidos. Se exige un minuto entre reservas y se admiten hasta tres reservas por quince minutos y cuenta, incluyendo fallos de transporte. Los límites sobreviven al reinicio y el reenvío no borra fallos. La limpieza futura de desafíos deberá conservar el historial necesario para estos límites.

El transporte se ejecuta fuera de la transacción. El estado pendiente no permite verificar el código; solo después de terminar el transporte y volver a comprobar la vigencia se marca enviado. Un error se marca como fallido e invalida el desafío. Una entrega tardía no amplía su plazo: caduca cinco minutos después de crearse. Que el transporte termine no demuestra que el usuario haya recibido o leído el mensaje.

verificar comprueba cuenta, rol, versión, correo, estado, caducidad, consumo previo y bloqueo. Compara el verificador con compare_digest y consume el código dentro de una transacción. Los errores de PIN en un desafío vigente incrementan el mismo contador de cuenta que los de contraseña y aplican la misma espera progresiva; al llegar a quince la cuenta queda bloqueada. Las solicitudes durante una espera no contabilizan otro fallo admitido. Los formatos inválidos se rechazan y cuentan como fallo cuando el desafío admite verificación.

El resultado PinVerificado es interno y no es una sesión ni una autorización que pueda presentarse desde el navegador. Indica si sigue pendiente cambiar la contraseña temporal. No se reinician fallos ni se modifica el último acceso completo. El cambio de contraseña del administrador continúa denegado hasta integrar una prueba de verificación consumible y el flujo de acceso; no se debe aceptar un objeto o booleano aportado por un cliente como sustituto.

Se han añadido catorce pruebas de PIN y dos de migración. Cubren generación, destino configurado, caducidad exacta, reenvío, límites persistentes, fallo de transporte, intento durante envío, bloqueo compartido, conservación de fallos, clave HMAC, cambios de cuenta, formatos inválidos, solicitudes y consumos concurrentes, migración y reversión.

La suite local ha superado 113 pruebas en Windows. El transporte de pruebas guarda mensajes ficticios únicamente en memoria con un destinatario example.invalid. No se ha enviado ningún correo real, configurado la dirección real del administrador ni creado una base de uso real. La prueba del proveedor de correo, sus tiempos de espera, la interfaz, las sesiones, la auditoría, recuperación y el límite global de peticiones continúan pendientes. No se ha ejecutado esta entrega en GitHub Actions.

Referencia: [secrets en Python 3.13](https://docs.python.org/3.13/library/secrets.html).

## Cambio obligatorio administrativo tras PIN — 8 de noviembre de 2026

El esquema versión 3 añade autorizaciones_cambio. Cada registro contiene el hash de un token aleatorio de 32 bytes, referencia única al desafío de PIN, creación, caducidad y consumo. Se han comprobado la migración desde versión 2 y la reversión si falla, conservando usuarios. Las bases nuevas y las de versión 1 recorren las migraciones necesarias en la transacción de inicialización.

Al consumir un PIN correcto de un administrador con cambio obligatorio se emite una autorización de cinco minutos exclusiva para ese cambio. Se devuelve su token original una sola vez en el resultado interno; su campo se excluye del repr del objeto. En SQLite solo se guarda SHA-256 del token de alta entropía. Este mecanismo no sustituye al HMAC del PIN de siete cifras: son secretos con espacios de valores diferentes.

cambiar_contrasena_administrador, en taxiroute/cambio_administrador.py, recibe el token y la contraseña nueva. No acepta usuario, rol, objetos PinVerificado ni indicadores de PIN correcto como prueba. La identidad se obtiene de las relaciones guardadas en SQLite. Se exige cuenta administradora habilitada, sin bloqueo ni espera, con cambio obligatorio, versión de credenciales y correo coincidentes, PIN consumido no invalidado y autorización sin consumir ni caducar.

La nueva contraseña debe cumplir la política y diferir de la actual. Argon2 se ejecuta fuera de la transacción. Antes de guardar se revisan otra vez las condiciones, incluyendo caducidad y credenciales. El nuevo hash, la retirada de cambio_obligatorio, el incremento de versión y el consumo del token se confirman juntos. Ante un fallo de escritura se revierten todos. Los fallos acumulados y ultimo_acceso_en no cambian.

El PIN y el token son dos usos únicos distintos: el PIN se consume al emitir la autorización; el token se consume al guardar la nueva contraseña. La autorización no permite otras operaciones ni inicia sesión. Si el administrador no tiene cambio obligatorio, verificar el PIN no emite una autorización de cambio. El cambio voluntario administrativo queda fuera de esta entrega.

Se han añadido once pruebas que cubren autorización auténtica, rechazo de datos inventados e identificadores de desafío, caducidad, contraseña inválida o repetida, cuenta bloqueada/desactivada, cambios de rol/correo/versión, fallo de escritura, dos cambios simultáneos, expiración durante Argon2, ausencia de cambio obligatorio y migración.

Se han superado las 124 pruebas locales en Windows. No se han creado cuentas reales, enviado correos ni conectado estas operaciones a la web. La futura integración deberá proteger el token como credencial temporal, vincular el flujo al navegador y aplicar controles de origen y CSRF. Excluirlo del repr no impide que otro código lo registre explícitamente; no debe aparecer en URL, registros ni almacenamiento persistente del navegador. Las sesiones, la auditoría y el proveedor real siguen pendientes.

## Política de sesiones aprobada — 8 de noviembre de 2026

Se fija una hora de inactividad y ocho horas de duración máxima para conductores y administradores. La duración absoluta se contará desde la creación de la sesión tras completar la autenticación. La última interacción admitida reiniciará únicamente el plazo de inactividad, nunca el de ocho horas. Se denegará el acceso cuando se alcance cualquiera de los dos límites, incluyendo el instante exacto de caducidad.

La consulta automática del contador y las peticiones de fondo no contarán como interacción humana. Los plazos se comprobarán en el servidor; el navegador no será la autoridad sobre la hora o vigencia de la sesión. La forma de comunicar interacciones admitidas se concretará al integrar la interfaz.

La caducidad, el bloqueo de acceso o el cierre de sesión no finalizarán una carrera. El servicio de cálculo deberá conservar su estado independientemente de la sesión, mientras el proceso siga ejecutándose. Esta decisión no garantiza continuidad durante suspensión, apagado o caída del proceso.

Después de caducar habrá que repetir el acceso: contraseña para conductores y contraseña más PIN para administradores, con cambio obligatorio si corresponde. El cierre manual y la revocación podrán invalidar una sesión antes de alcanzar los límites.

Ejemplos: una sesión creada a las 09:00 sin interacción posterior caducará a las 10:00. Si mantiene actividad admitida sin intervalos de una hora, caducará como máximo a las 17:00. La hora de una interacción recibida después de caducar no reactivará la sesión.

Esta sección registra la decisión de diseño. Las sesiones y su integración web todavía no están implementadas; los 124 tests previos no acreditan esta política. Las pruebas futuras cubrirán ambos límites, su instante exacto, la actividad automática y la conservación de las carreras.

## Sesiones internas — 8 de noviembre de 2026

El esquema versión 4 añade sesiones mediante migración transaccional, conservando los registros anteriores. La tabla contiene identificador, hash del token, usuario, versión de credenciales, rol y correo administrativo de referencia, creación, última interacción, caducidad absoluta y revocación. El token original de 32 bytes aleatorios solo se devuelve en SesionEmitida, excluido de su representación textual; la base almacena SHA-256.

ServicioSesiones.iniciar_conductor vuelve a verificar contraseña y exige rol conductor, cuenta disponible y ausencia de cambio obligatorio. No acepta un objeto de identidad aportado por el cliente. La comprobación se repite al escribir. Si el acceso se completa, crear la sesión, reiniciar fallos y registrar el último acceso son una sola transacción.

ServicioPIN.verificar ahora emite una sesión en esa misma transacción cuando el administrador no tiene cambio obligatorio. Si necesita cambiar la contraseña, emite únicamente la autorización de cambio, como antes. Tras cambiarla se debe completar un nuevo acceso con contraseña y PIN para obtener sesión; el cambio por sí solo no inicia acceso. Un fallo de creación de sesión revierte también el consumo del PIN y el reinicio de fallos.

consultar comprueba vigencia en el servidor. Aplica una hora desde la última interacción admitida y ocho horas desde la creación, rechazando los instantes exactos de caducidad. Las consultas automáticas no actualizan interacción. Una interacción recibida cuando ya ha caducado no reactiva la sesión. Se rechaza un reloj anterior a la última interacción registrada; la exactitud del reloj del equipo sigue siendo una dependencia operativa.

Se comprueban cuenta habilitada, bloqueo, contador, cambio obligatorio, rol, versión de credenciales y correo administrativo. Si dejan de coincidir, la consulta registra revocación y deniega acceso. Los cambios de cuenta que aún no están implementados deberán revocar sesiones atómicamente: si se desactiva y reactiva una cuenta sin consultar su sesión entre ambas acciones, la consulta por sí sola no registra el estado intermedio.

cerrar revoca únicamente el token indicado y no detiene procesos ni carreras. Las sesiones se conservan al reinstanciar el servicio dentro de sus plazos. La política de sesión única por cuenta permanece aplazada: actualmente pueden coexistir varias sesiones válidas.

Se han añadido doce pruebas de sesiones y una de migración. Cubren creación, secretos fuera del resultado textual y almacenamiento, límites de caducidad, consultas automáticas, interacción, cierre, cambio de contraseña, estado de cuenta, persistencia, tokens falsos, PIN administrativo, contraseña temporal, fallo transaccional y retroceso del reloj. La prueba de carrera utiliza el servicio de cálculo separado y verifica que el cierre o caducidad no lo modifica; no acredita todavía la integración con la pantalla web.

La suite local ha superado 137 pruebas en Windows. No se han creado cuentas reales ni enviado correos. Las cookies, HTTPS, CSRF del flujo de acceso, vinculación al navegador, clasificación de interacción humana, permisos de rutas, auditoría y pruebas de navegador siguen pendientes. El argumento interaccion es interno: no debe tomarse de un campo libre del cliente. La consulta actual serializa sus operaciones mediante una transacción de escritura; su rendimiento deberá medirse antes de habilitar acceso remoto.

## Administración interna de conductores — 8 de noviembre de 2026

El esquema versión 5 añade auditoria_cuentas. La función administrar_conductor de taxiroute/administracion.py comprueba dentro de la misma transacción la sesión administrativa, rol, cuenta, correo, versión, inactividad y caducidad absoluta. Solo acepta cuentas con rol conductor como destino. No es una ruta web ni incluye creación de cuentas o restablecimiento de contraseñas.

Desactivar revoca las sesiones del conductor; reactivar no elimina bloqueos. Desbloquear limpia los fallos y la espera, sin reactivar una cuenta deshabilitada. Revocar invalida sesiones sin desactivar la cuenta. Los cambios y el evento de auditoría con actor, afectado, acción y fecha se confirman juntos; un fallo de auditoría revierte toda la operación. Los eventos no contienen contraseñas ni tokens.

No se eliminan cuentas ni se modifica el historial. La implementación no integra aún las carreras activas con las cuentas; conservar y gestionar esas carreras requiere completar esa integración. Las operaciones administrativas aún no registran la interacción de sesión ni exigen reautenticación reciente para cambios sensibles. La consulta de validez reproduce actualmente las reglas de sesiones y deberá unificarse antes de exponer rutas para evitar divergencias.

Se han superado 143 pruebas locales en Windows. Las seis pruebas nuevas cubren permisos, sesión caducada, acciones, independencia de bloqueo y desactivación y reversión de cambios si falla la auditoría. No se han creado cuentas reales ni ejecutado las nuevas pruebas en GitHub Actions.


## Validación compartida y ayuda contextual — 8 de noviembre de 2026

La comprobación de vigencia se ha centralizado en _identidad_vigente, dentro de sesiones.py. La consulta de sesiones y la administración la invocan con su conexión y transacción actuales. Así aplican las mismas reglas de cuenta, rol, credenciales, correo y caducidad sin abrir una transacción adicional. La consulta puede revocar una sesión inválida; la operación administrativa deniega y revierte la transacción.

Se ha añadido una prueba de rechazo por cambio de correo tanto en administración como al consultar la sesión. La suite completa anterior de 143 pruebas ha pasado tras la refactorización; la prueba específica se ejecuta después como parte de las pruebas de administración.

Los textos de ayuda solicitados para los botones y sus requisitos de acceso por puntero, teclado y dispositivo táctil se han registrado en ALCANCE_FASE_3.md. Aún no existe el panel administrativo ni se han comprobado esos elementos visuales.


## Preparación del acceso web

Se ha añadido taxiroute/web_acceso.py con un decorador para rutas Flask. Lee exclusivamente la cookie de sesión, consulta su vigencia en el servicio y comprueba los roles fijados por la propia ruta. Una sesión ausente o inválida produce 401; una identidad sin el rol requerido produce 403. La identidad validada se guarda en g para la petición.

Este componente no se ha conectado a crear_app ni cambia las rutas /prototipo existentes, que siguen sin autenticación. No crea ni configura cookies y no incluye protección CSRF. Las rutas que escriban datos deberán verificar origen y proteger solicitudes cruzadas. Las comprobaciones no renuevan actividad; el registro seguro de interacción queda pendiente. Los tests usan una aplicación Flask aislada y cuentas temporales, no la aplicación de uso.


## Prototipo aislado de acceso de conductor

web_login.py ofrece un blueprint Flask experimental que valida credenciales de conductores sin cambio obligatorio y crea una cookie Secure, HttpOnly y SameSite=Strict después de iniciar sesión. Exige HTTPS, Origin igual al origen del servidor, rechaza Sec-Fetch-Site cross-site y recibe JSON con las claves exactas esperadas. La aplicación integradora debe restringir hosts, limitar peticiones, limitar el tamaño de solicitudes, proteger todas las operaciones y configurar HTTPS.

Los errores no distinguen cuentas inexistentes, bloqueadas o contraseñas incorrectas. El flujo no permite aún cambiar la contraseña temporal ni autenticar administradores. No ofrece cierre de sesión web. El blueprint no está registrado en la aplicación principal: el prototipo web existente sigue sin autenticación. Los tests usan HTTPS simulado en el cliente Flask y una base temporal; no acreditan certificados ni pruebas reales de navegador.


## Prototipo aislado de cierre de sesión web

El blueprint experimental ofrece POST /acceso/cerrar. Verifica HTTPS y el origen, revoca en el servidor la sesión correspondiente a la cookie y después solicita borrarla con Path=/, Secure, HttpOnly y SameSite=Strict. Repetirlo sin una sesión es seguro. Si falla la revocación no se devuelve confirmación ni se borra la cookie.

Cinco pruebas aisladas cubren revocación y reutilización, cierre repetido, rechazo por origen o HTTP, una carrera que continúa en un servicio separado y un error simulado de almacenamiento. Esta prueba de carrera no demuestra la integración de sesiones con la web actual. HTTPS está simulado: faltan certificados, interfaz, cierre real en navegador y registro del blueprint en la aplicación principal.


## Prototipo aislado de acceso administrativo web

web_admin_login.py incorpora un blueprint experimental no registrado en la aplicación principal. Con contraseña correcta reserva un desafío y devuelve una cookie temporal HttpOnly, Secure y SameSite=Strict, limitada a /acceso/administrador. La respuesta no contiene el PIN ni el identificador. El código se entrega al transporte inyectado, simulado en las pruebas.

POST /acceso/administrador/pin obtiene el desafío desde la cookie y lo verifica. Si la contraseña necesita cambio obligatorio devuelve una cookie temporal para ese cambio, no una sesión. Si no, entrega la cookie de sesión tras el PIN. El PIN se consume una vez. POST /acceso/administrador/cambio permite completar el cambio usando la cookie temporal; después se exige iniciar acceso de nuevo.

Las tres rutas requieren HTTPS, Origin coincidente, JSON con claves exactas y rechazan Sec-Fetch-Site cross-site. Las respuestas del blueprint indican no-store. La aplicación integradora deberá validar hosts, limitar el tamaño y frecuencia de peticiones, proteger los demás endpoints, gestionar errores del transporte y configurar TLS real. Las cookies temporales son credenciales portadoras y deberán protegerse contra robo o fijación en la integración; no se ha probado el flujo en navegador real.

Se han superado 165 pruebas locales en Windows, incluidas seis nuevas con correo simulado y HTTPS del cliente de Flask. Cubren contraseña y PIN, errores y caducidad, falta de cookie, cambio temporal, origen y ausencia de caché. No hay envío real, cuenta administrativa real, certificado instalado ni integración con el prototipo principal, que sigue sin autenticación.


## Adaptador Gmail simulado

Se ha elegido Gmail API con OAuth y permiso gmail.send. La misma cuenta será inicialmente remitente y destinataria; la dirección real y las credenciales no se incluyen en el repositorio. correo_gmail.py incorpora EnviadorGmail, que recibe un cliente ya autorizado y construye un correo de PIN para users.messages.send. No configura OAuth ni incluye bibliotecas de Google; no se ha autorizado ninguna cuenta.

El destinatario procede del servicio PIN y se valida como una única dirección sin cabeceras adicionales. El mensaje se codifica en base64 URL conforme al formato de Gmail. El adaptador no imprime el PIN ni los detalles del error del proveedor. Si falla, ServicioPIN invalida el desafío.

Cinco pruebas con un cliente simulado verifican estructura del mensaje, validación, fallo y conexión con ServicioPIN. No envían mensajes ni comprueban OAuth, conexión de red, plazos o entrega. Esos pasos y la protección del token OAuth permanecen pendientes.

La preparación de OAuth y sus límites se detallan en [CONFIGURACION_GMAIL.md](CONFIGURACION_GMAIL.md). El flujo de autorización y el almacén de tokens aún no están implementados.


## Preparación OAuth de escritorio

El flujo para Windows y su configuración del almacén se han preparado en taxiroute/oauth_gmail.py. La guía CONFIGURACION_GMAIL.md distingue las pruebas simuladas de la autorización real pendiente. La suite completa ha superado 176 pruebas locales en Windows.


## Interfaz aislada de acceso administrativo

Se ha preparado una aplicación de prueba mediante crear_app_demo_acceso, en taxiroute/web_demo_acceso.py. Recibe el servicio de PIN y el de sesiones; no arranca un servidor ni configura cuentas o envíos por sí misma. Las plantillas demo_acceso.html y demo_panel.html y sus recursos CSS y JavaScript ofrecen los pasos de contraseña, PIN y cambio obligatorio, seguidos de un panel protegido de demostración.

El panel exige una sesión administrativa. La cuenta, el buzón y la base utilizados por las pruebas son temporales. Las cinco pruebas de test_demo_acceso.py comprueban acceso sin sesión, contraseña incorrecta, PIN correcto y reutilizado, caducidad, cambio obligatorio, escape del nombre y host permitido. La sintaxis de JavaScript se ha comprobado con node --check.

La suite local completa supera 181 pruebas en Windows. Las pruebas de Flask simulan HTTPS: no prueban TLS real, JavaScript en navegador, accesibilidad ni el envío real en este recorrido. La fábrica no se ha conectado a la web principal, que sigue sin autenticación. Antes de una demostración real faltan HTTPS, configuración de ejecución aislada y validación visual y funcional; no utilizar credenciales reales en esta maqueta.

## Prototipo local de separación de carreras — 8 de noviembre de 2026

Para estudiar la flota sin alterar todavía la web ni el CSV se ha añadido `FlotaEnMemoria` en `taxiroute/flota.py`. Asigna una instancia independiente de `ServicioCarreras` a cada identificador interno y serializa sus operaciones dentro del mismo proceso. El identificador tendrá que proceder de una sesión comprobada por el servidor: este componente no autentica ni autoriza usuarios.

| Alternativa | Decisión para este paso | Consecuencias y revisión |
| --- | --- | --- |
| Una única instancia compartida | Descartada para la flota | Es la organización actual de la web y solo representa una carrera activa. |
| Una instancia por usuario, en memoria | Elegida como prototipo | Reutiliza el cálculo y permite experimentar con cambios acotados. Pierde el estado al detenerse el proceso; el bloqueo único serializa las operaciones y los servicios permanecen en memoria. No se ha medido su capacidad. |
| Persistencia coordinada en base de datos | Pendiente de diseño | Puede facilitar recuperación y coordinación entre procesos, pero exige decidir transacciones, migración, atribución y recuperación; no se ha implementado ni se ha estimado su coste. |

Las pruebas cubren dos carreras solapadas, mantenimiento de tarifas por carrera, dos intentos simultáneos de inicio para un usuario y rechazo de una fábrica que devuelve el mismo servicio para dos usuarios. Se usa un reloj controlado. La prueba concurrente cubre dos hilos en un único proceso, no varios procesos ni dispositivos. No se atribuye el historial CSV a conductores ni se conectan aquí cuentas, vehículos o sesiones a la flota. Antes de la integración habrá que definir la recuperación tras cierre, la propiedad del historial, la identidad verificada, el ciclo de vida y los límites de memoria.

## Consulta experimental de carreras por sesión — 8 de noviembre de 2026

La aplicación permite inyectar `flota_prueba` junto con `sesiones_prueba` en el modo de integración parcial. En la consulta de estado, el decorador valida la sesión y coloca la identidad en el contexto de la petición; la ruta utiliza ese identificador para consultar la flota y rechaza parámetros en la dirección. Las demás rutas de la web siguen bloqueadas en esta integración. El modo habitual de la aplicación no incorpora esta conexión y continúa sin autenticación.

Se han probado dos clientes con cuentas temporales, carreras y relojes controlados: cada uno recibe su estado, no puede elegir otro identificador en la consulta, y cerrar una sesión no detiene las carreras. También se comprueba la caducidad. Las carreras se crean directamente desde las pruebas, no desde la web. Las peticiones usan el cliente de Flask con HTTPS simulado; no demuestran comportamiento en navegador, seguridad frente a todos los ataques, persistencia ni acceso remoto. Siguen pendientes las acciones por usuario, la vinculación con vehículos y el historial atribuido.

## Acciones experimentales por sesión — 8 de noviembre de 2026

En la integración parcial, la ruta `/prueba/flota/accion` acepta iniciar, mover, parar y finalizar para la identidad validada de la sesión. Requiere HTTPS, origen coincidente y JSON con la única clave `accion`; rechaza parámetros de URL, campos adicionales y peticiones identificadas como de origen cruzado. La ruta no se registra en el modo normal. No admite todavía reintento, exportación, historial ni gestión de tarifas.

Las pruebas con cuentas temporales verifican dos carreras separadas, intentos de indicar otro usuario, origen incorrecto, sesión ausente o caducada y datos no válidos. Utilizan el cliente de Flask con HTTPS simulado y un servicio en memoria sin historial persistente: no prueban el navegador, recuperación ni guardado de carreras. El decorador comprueba la sesión antes de la operación, pero aún falta resolver y probar la coordinación entre revocación y una acción simultánea; tampoco se ha incorporado control de revisión para peticiones duplicadas o desactualizadas. Esta integración no debe considerarse lista para uso real ni acceso remoto.

## Revisiones experimentales por conductor — 8 de noviembre de 2026

La consulta de flota devuelve una revisión por usuario, inicialmente cero. Las acciones experimentales requieren un entero no negativo con la revisión observada. `FlotaEnMemoria` compara el número y ejecuta la orden bajo el mismo bloqueo; si no coincide devuelve un conflicto HTTP 409 y no ejecuta la orden. Cada operación procesada incrementa la revisión de ese usuario, incluso si el servicio informa de que no había nada que cambiar. La revisión de otro usuario no cambia.

Las pruebas cubren una orden antigua contra una carrera posterior, una petición duplicada y dos hilos que compiten con la misma revisión. La revisión está en memoria, se pierde al reiniciar y solo coordina este proceso. No equivale a una clave de idempotencia ni resuelve una respuesta perdida, la revocación simultánea de sesión, la persistencia, varios procesos o la integración visual. Esos aspectos siguen pendientes antes de utilizar la flota con datos reales.

## Orden experimental entre sesión y acción — 8 de noviembre de 2026

En la ruta de flota de prueba se vuelve a validar la sesión inmediatamente antes de ejecutar la operación. La segunda validación utiliza una transacción SQLite de escritura que también serializa los cierres y las operaciones administrativas que usan esa misma base. Si una revocación termina antes de que la acción obtenga la transacción, la acción se deniega. Si la acción obtiene primero la transacción, la revocación debe esperar a que termine. El orden depende de cuál obtiene primero la transacción, no del momento en que el usuario pulsa un botón.

Las pruebas cubren ambos órdenes con hilos y bases temporales, y simulan una revocación entre las dos comprobaciones de la ruta. Esta coordinación es experimental: mantiene el bloqueo de escritura durante la operación, puede provocar esperas o errores por tiempo agotado, y no debe usarse para operaciones largas o que abran otra transacción de escritura en la misma base. SQLite no puede deshacer cambios en memoria o archivos si falla el commit; una excepción o caída durante la operación necesita tratamiento adicional. No se han probado varios procesos, recuperación tras caída ni persistencia de carreras. Antes del uso real hay que diseñar transacciones y recuperación con el almacenamiento definitivo.

## Decisión de recuperación — 8 de noviembre de 2026

Se ha acordado mantener el historial CSV en la fase 3 y ampliar SQLite para carreras activas, pendientes y su relación con el conductor. Tras una interrupción se recuperará el último punto guardado, sin sumar automáticamente el tiempo no registrado, y se pedirá revisión. El [diseño de recuperación](DISENO_RECUPERACION.md) describe la propuesta, las decisiones pendientes y sus límites; todavía no está implementado.

### Desactivación con una carrera pendiente (11 de noviembre de 2026)

Antes de desactivar un conductor, la aplicación comprueba si tiene una carrera activa, un cierre pendiente o una acción que necesita revisión. En cualquiera de esos casos, pide finalizar o revisar la situación y no desactiva la cuenta ni revoca sus sesiones. Si no puede comprobar el estado, tampoco desactiva la cuenta. Cuando no existe ninguna situación pendiente, la desactivación revoca las sesiones como se describió anteriormente.

La comprobación está coordinada con las operaciones de carrera de la misma instalación y se ha probado con datos temporales. La revocación de sesiones sigue siendo una acción distinta y no detiene la carrera. Esta regla implica que una desactivación por motivos de seguridad puede tener que esperar hasta resolver la carrera; el proceso de respuesta urgente queda por definir.
