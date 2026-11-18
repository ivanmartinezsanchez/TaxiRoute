# Revisión de la fase 4 para la entrega

Revisión del 11 de noviembre de 2026. El objetivo es mostrar el estado de la rama de fase 4 frente al enunciado, distinguiendo la demostración académica de una instalación para uso real.

| Requisito | Situación comprobada | Pendiente o límite |
| --- | --- | --- |
| Historial en base de datos relacional | La demo Docker utiliza SQLite y un volumen persistente. Tras reiniciar el contenedor, la carrera registrada seguía presente y la comprobación de integridad respondió `ok`. | La migración de historiales de uso a SQLite sigue siendo experimental y no se ha realizado con datos reales. |
| API para carreras e historial | Con un cliente HTTP independiente y cuentas ficticias se han comprobado el rechazo sin sesión, el acceso, inicio, cambio de estado, finalización y consulta del historial. | Usa sesión y origen de la aplicación. El contrato y la autenticación para integraciones externas no están definidos como estables. El cierre no garantiza reintento idempotente. |
| Panel web de flota | Hay un panel administrativo para consultar el historial de la demo; se ha probado con datos ficticios. | El PIN de la demo aparece en los registros del contenedor y no se envía por correo. No es una configuración para datos reales. |
| Un comando y persistencia | `docker compose -f compose.sqlite-demo.yaml up --build -d` arranca la demo; el volumen conserva el historial al reiniciar el contenedor. | Requiere Docker y la imagen debe poder construirse. No es una instalación completa y segura para uso real. No se ha comprobado un reinicio del sistema operativo en esta prueba. |
| Acceso desde dispositivos de la red del taxi | Existe una opción experimental en el servidor SQLite fuera de la demo Docker; se ha probado con solicitudes simuladas. | La demo Docker solo publica el servicio en el propio ordenador. Faltan configuración protegida, certificado confiable y una prueba desde otro dispositivo. |
| Documentación y entrega | El repositorio incluye enunciado, documentación técnica, instrucciones de la demo y enlace al tablero. | Existe un manual para la demostración; falta completarlo para la instalación final y revisar el tablero y las pruebas de la versión entregada. |

## Actualización del 15 de noviembre

- Se ha abierto la solicitud de integración [#18](https://github.com/ivanmartinezsanchez/TaxiRoute/pull/18) como borrador. Las fases 3 y 4 siguen en la rama `feature/fase-4-produccion`; todavía no están integradas en `main`.
- Se ha comprobado en un teléfono Android una carrera ficticia por la red local: inicio, cambio de estado, finalización y consulta en el historial. Se utilizó una instancia aislada con certificado autofirmado; no acredita que el certificado de la instalación propia sea confiable ni que esa instalación esté disponible desde el teléfono.
- El acceso de administrador con PIN enviado por Gmail se ha comprobado en Docker Compose con cuenta ficticia. También se ha preparado una instalación local con cuenta propia, cuyo acceso se ha comprobado. La carga del perfil de Gmail requiere la opción explícita documentada.
- El arrancador de Windows puede crear el entorno Python e instalar dependencias cuando no existe, y respeta los entornos existentes. Sigue necesitando Docker Desktop y Python 3.13; el [manual local](MANUAL_LOCAL_TAXIROUTE.md) describe su uso.
- La actualización segura de la imagen y los datos de la instalación existente, la recuperación completa, la confianza HTTPS en Android y el acceso de esa instalación desde otros dispositivos siguen pendientes. El código actualizado en GitHub no sustituye automáticamente los contenedores existentes.

La tabla anterior conserva la fotografía de la revisión del 11 de noviembre. Para el detalle más reciente de las pruebas y sus límites, consulta [Estado de entrega](ESTADO_ENTREGA.md).

## Seguridad y alcance

La demo usa cuentas y contraseñas ficticias conocidas, un PIN visible en los registros, un certificado autofirmado y un servidor de desarrollo. Se limita a `127.0.0.1`; no debe abrirse a otros dispositivos ni emplearse con datos reales. La prueba de la API usó una excepción de verificación del certificado únicamente para el cliente local de prueba; no acredita TLS confiable. La conexión desde ubicaciones distintas queda fuera de esta entrega por decisión previa.

El enunciado también pide una interfaz usable en tablet. Por decisión del proyecto no se realizará una prueba física en este dispositivo; se debe presentar esa limitación con claridad.

## Avance del 12 de noviembre

Hay un Compose experimental separado para una instalación SQLite existente, con archivos TLS y OAuth externos y publicación limitada al ordenador. Con datos ficticios se ha probado su arranque y una consulta HTTPS sin sesión desde el propio contenedor. No demuestra acceso desde otro dispositivo ni envío de Gmail desde Docker. El montaje de un archivo ficticio de solo lectura se comprobó por separado en Docker Desktop. Los secretos de Compose local no cifran el archivo del equipo.

Un recorrido posterior del Compose experimental con un cliente HTTPS de Windows comprobó acceso de conductor, carrera, historial y persistencia tras reiniciar, además del rechazo administrativo con OAuth ficticio inválido. El cliente tuvo que conectar a localhost usando una autoridad IP privada configurada para TLS; no es acceso normal desde el navegador ni una prueba desde otro dispositivo.

Después se ha corregido la autoridad del Compose local: la URL `https://127.0.0.1:18770` y el acceso de conductor se han probado desde un cliente HTTPS en Windows con certificado ficticio verificado. Sigue siendo experimental y no se ha probado con navegador, datos reales ni desde la red del taxi.

El acceso administrativo con correo real se ha comprobado en un contenedor temporal con cuenta ficticia: se recibió el PIN y se accedió al panel. No equivale todavía a validar Compose con datos persistentes reales ni a una instalación de producción.

Posteriormente se verificó el acceso administrativo con PIN por Gmail real en Compose, con cuentas ficticias y datos temporales. Quedan pendientes la preparación de una instalación de uso real, el acceso desde otro dispositivo y la gestión de la autorización después de reinicios o caducidad.
