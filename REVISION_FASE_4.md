# Revisión de la fase 4 para la entrega

Revisión del 11 de noviembre de 2026. El objetivo es mostrar el estado de la rama de fase 4 frente al enunciado, distinguiendo la demostración académica de una instalación para uso real.

| Requisito | Situación comprobada | Pendiente o límite |
| --- | --- | --- |
| Historial en base de datos relacional | La demo Docker utiliza SQLite y un volumen persistente. Tras reiniciar el contenedor, la carrera registrada seguía presente y la comprobación de integridad respondió `ok`. | La migración de historiales de uso a SQLite sigue siendo experimental y no se ha realizado con datos reales. |
| API para carreras e historial | Con un cliente HTTP independiente y cuentas ficticias se han comprobado el rechazo sin sesión, el acceso, inicio, cambio de estado, finalización y consulta del historial. | Usa sesión y origen de la aplicación. El contrato y la autenticación para integraciones externas no están definidos como estables. El cierre no garantiza reintento idempotente. |
| Panel web de flota | Hay un panel administrativo para consultar el historial de la demo; se ha probado con datos ficticios. | El PIN de la demo aparece en los registros del contenedor y no se envía por correo. No es una configuración para datos reales. |
| Un comando y persistencia | `docker compose -f compose.sqlite-demo.yaml up --build -d` arranca la demo; el volumen conserva el historial al reiniciar el contenedor. | Requiere Docker y la imagen debe poder construirse. No es una instalación completa y segura para uso real. No se ha comprobado un reinicio del sistema operativo en esta prueba. |
| Acceso desde dispositivos de la red del taxi | Existe una opción experimental en el servidor SQLite fuera de la demo Docker; se ha probado con solicitudes simuladas. | La demo Docker solo publica el servicio en el propio ordenador. Faltan configuración protegida, certificado confiable y una prueba desde otro dispositivo. |
| Documentación y entrega | El repositorio incluye enunciado, documentación técnica, instrucciones de la demo y enlace al tablero. | Falta el manual de usuario final y revisar y actualizar el tablero y los resultados de pruebas de la versión entregada. |

## Seguridad y alcance

La demo usa cuentas y contraseñas ficticias conocidas, un PIN visible en los registros, un certificado autofirmado y un servidor de desarrollo. Se limita a `127.0.0.1`; no debe abrirse a otros dispositivos ni emplearse con datos reales. La prueba de la API usó una excepción de verificación del certificado únicamente para el cliente local de prueba; no acredita TLS confiable. La conexión desde ubicaciones distintas queda fuera de esta entrega por decisión previa.

El enunciado también pide una interfaz usable en tablet. Por decisión del proyecto no se realizará una prueba física en este dispositivo; se debe presentar esa limitación con claridad.

## Avance del 12 de noviembre

Hay un Compose experimental separado para una instalación SQLite existente, con archivos TLS y OAuth externos y publicación limitada al ordenador. Con datos ficticios se ha probado su arranque y una consulta HTTPS sin sesión desde el propio contenedor. No demuestra acceso desde otro dispositivo ni envío de Gmail desde Docker. El montaje de un archivo ficticio de solo lectura se comprobó por separado en Docker Desktop. Los secretos de Compose local no cifran el archivo del equipo.
