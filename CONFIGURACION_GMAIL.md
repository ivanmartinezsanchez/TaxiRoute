# Configuración de Gmail para TaxiRoute

## Estado inicial de preparación

Los siguientes apartados describen la preparación inicial. La prueba real autorizada se recoge al final de este documento.

Se ha elegido Gmail API con OAuth 2.0 para enviar el PIN administrativo. El destinatario se configura de forma privada; inicialmente será la misma cuenta autorizada como remitente.

El adaptador y el flujo OAuth están implementados y probados con clientes y almacén simulados. El flujo está preparado para guardar tokens en el Administrador de credenciales de Windows, pero no se ha realizado una autorización real ni una prueba de almacenamiento real. No se ha registrado un proyecto en Google Cloud ni enviado correos reales.

## Permiso previsto

La aplicación solicitará únicamente:

    https://www.googleapis.com/auth/gmail.send

Este permiso permite enviar mensajes en nombre de la cuenta autorizada. No se solicitará el alcance general https://mail.google.com/ para esta función. El permiso gmail.send se clasifica como sensible y su uso está sujeto a las políticas y a los requisitos de Google.

## Preparación prevista inicialmente en Google Cloud

1. Crear o seleccionar un proyecto para el entorno de desarrollo.
2. Activar la API de Gmail.
3. Configurar la pantalla de consentimiento y su audiencia. Para una cuenta personal se evaluará la opción externa en modo de prueba y se añadirá la cuenta como usuario de prueba cuando corresponda.
4. Registrar un cliente OAuth de aplicación de escritorio para el flujo local.
5. Guardar el archivo de configuración descargado fuera del repositorio y con acceso restringido.
6. En un paso posterior, cuando el flujo esté implementado, autorizar desde el navegador la cuenta remitente y revisar el permiso de envío.

La guía rápida de Google menciona audiencia interna para organizaciones. Esa opción no debe darse por disponible para una cuenta personal. La pantalla puede variar según la cuenta y la configuración del proyecto. Una aplicación en prueba con permisos sensibles puede mostrar avisos y tener restricciones; no se presupone que esté verificada ni que los tokens duren indefinidamente.

## Protección de credenciales

El archivo del cliente OAuth y los tokens no se incluirán en Git ni en capturas o registros. Se guardarán fuera de la carpeta del proyecto. El archivo del cliente identifica la aplicación; las credenciales de sesión y especialmente el token de renovación permiten actuar en nombre de la cuenta autorizada y deben protegerse.

Para Windows se ha preparado el uso del Administrador de credenciales mediante keyring. Se rechazan otros backends hasta evaluarlos; macOS y Linux siguen pendientes. No se han guardado credenciales reales. Las exclusiones de .gitignore son una protección adicional: no cifran archivos, no restringen permisos y no eliminan secretos ya seguidos por Git. No introducir secretos en este documento ni en el chat.

El flujo de escritorio deberá usar PKCE y comprobar de forma segura la respuesta de autorización. La revocación y recuperación ante caducidad se deberán probar. No se almacenarán códigos PIN ni tokens en registros de actividad.

## Comprobación prevista inicialmente

La primera prueba real, una vez configurada y autorizada la cuenta, consistirá en solicitar un PIN desde TaxiRoute, confirmar su recepción y verificar que no se pueda reutilizar. Se revisarán también errores de red y autorización revocada. El envío real no forma parte de la preparación actual.

## Referencias

- [Guía de Gmail API para Python](https://developers.google.com/workspace/gmail/api/quickstart/python).
- [Permisos de Gmail API](https://developers.google.com/workspace/gmail/api/auth/scopes).
- [OAuth para aplicaciones de escritorio](https://developers.google.com/identity/protocols/oauth2/native-app).
- [Aplicaciones no verificadas](https://support.google.com/cloud/answer/7454865).


## Cuotas y costes — revisión de 8 de noviembre de 2026

Google indica que el uso estándar de Gmail API está disponible sin coste adicional, sujeto a cuotas de uso. La documentación también anuncia posibles cargos por superar umbrales posteriormente; se deben revisar las condiciones vigentes antes de activar facturación o incrementar el volumen. Los límites de envío de Gmail se aplican aparte. No se presupone que todas las funciones de Google Cloud sean gratuitas.

La suscripción de consumo a Google AI Pro y la facturación de Google Cloud o de Gemini API deben comprobarse por separado. La prueba gratuita de Cloud, si la cuenta cumple las condiciones, ofrece 300 USD de crédito durante 90 días. No se ha comprobado si la cuenta del proyecto cumple los requisitos ni se ha iniciado la prueba.

Las alertas de presupuesto no detienen automáticamente el gasto. No se habilitará facturación ni se vinculará un método de pago como parte de esta preparación. La aplicación de TaxiRoute continuará en local; utilizar Gmail API para enviar PIN no equivale a alojarla en Google Cloud.

Referencias: [cuotas de Gmail API](https://developers.google.com/workspace/gmail/api/reference/quota), [prueba gratuita de Google Cloud](https://cloud.google.com/signup-faqs), [alertas de presupuesto](https://docs.cloud.google.com/billing/docs/how-to/budgets) y [facturación de Gemini API](https://ai.google.dev/gemini-api/docs/billing).


Google anunció que Google AI Pro incluye beneficios del Google Developer Program, entre ellos 10 USD mensuales en créditos de Google Cloud, sujetos a activación y condiciones. Deben verificarse la elegibilidad, la disponibilidad y las condiciones de la cuenta antes de utilizarlos; no se ha activado ni comprobado este beneficio en TaxiRoute. Los créditos de la suscripción no equivalen a uso ilimitado.

Referencia: [beneficios para suscriptores de Google AI Pro y Ultra](https://blog.google/innovation-and-ai/technology/developers-tools/gdp-premium-ai-pro-ultra/).


## Implementación preparada — 8 de noviembre de 2026

taxiroute/oauth_gmail.py utiliza las bibliotecas de Google para preparar OAuth de escritorio con PKCE y alcance gmail.send. La autorización solo se inicia al llamar expresamente a autorizar_gmail. Se configura retorno local en 127.0.0.1, puerto disponible y espera máxima de 180 segundos. No se registra la URL de retorno de OAuth durante el flujo.

Si ya existe una autorización en el almacén no se sobrescribe. Si Google no devuelve token de renovación no se guarda una autorización incompleta. La sugerencia de cuenta solo orienta el selector de Google: no verifica qué cuenta se autorizó. Durante el consentimiento habrá que comprobarla visualmente. El proceso de revocación y reemplazo controlado permanece pendiente.

cliente_gmail_autorizado lee del almacén de Windows y, si las credenciales han caducado, solicita su renovación y guarda el resultado actualizado. No abre el navegador. Ambas funciones necesitan una autorización real válida y no están conectadas aún al arranque de TaxiRoute.

Se han instalado google-api-python-client, google-auth-oauthlib y keyring en el entorno virtual y añadido sus rangos a requirements.txt. pip check no ha detectado dependencias incompatibles. Seis pruebas sustituyen el cliente de Google y el almacén por objetos simulados; cubren PKCE configurado, bloqueo de sobrescritura, ausencia de token de renovación, renovación simulada y rechazo de backends no verificados. La suite local completa ha superado 176 pruebas en Windows.

No se ha abierto el navegador para OAuth, creado un cliente de Google Cloud, guardado un token real o enviado un correo. La autorización real, seguridad operativa, recuperación, confirmación de cuenta, pruebas del Administrador de credenciales con datos de prueba, y soporte para macOS y Linux siguen pendientes.


## Prueba real de entrega — 8 de noviembre de 2026

Se ha creado un cliente OAuth de escritorio para el proyecto de desarrollo, con Gmail API habilitada y el permiso gmail.send registrado. La aplicación permanece en modo de prueba. El propietario de la cuenta ha autorizado el envío; las credenciales de renovación se guardan en el Administrador de credenciales de Windows. La cuenta remitente y la destinataria se han confirmado por el usuario.

Se ha enviado un único código de prueba a la cuenta destinataria mediante Gmail API. La API aceptó la solicitud y el usuario confirmó que recibió el mensaje. La prueba utilizó una cuenta y una base SQLite temporales que se eliminaron al terminar; ese código no sirve para iniciar sesión en TaxiRoute.

Esta comprobación acredita la autorización y la entrega de ese mensaje, no garantiza la entrega futura ni prueba el acceso completo en la interfaz. La caducidad y el uso único cuentan con pruebas simuladas; falta verificarlos de extremo a extremo en la web integrada. No se almacenan aquí direcciones privadas, tokens, códigos ni credenciales.

## Recarga temporal en Compose experimental

El Compose temporal experimental no conserva la autorización de Gmail al reiniciar. Una vez preparado y arrancado ese Compose por separado, desde la carpeta del proyecto y con el entorno virtual activo se puede ejecutar `python -m taxiroute.cargar_gmail_windows` para cargar el perfil alternativo desde el Administrador de credenciales de Windows. El cargador verifica el contenedor y no imprime ni escribe el token en un archivo permanente de Windows. Se ha comprobado la recarga en una instalación ficticia; el acceso administrativo completo con correo real se comprobó por separado.

La carga no arranca el servicio ni configura cuentas o certificados. Debe repetirse tras reiniciar el contenedor. El archivo temporal puede ser accesible a administradores del equipo, al daemon Docker y a procesos del mismo usuario del contenedor; tmpfs no garantiza que no exista intercambio a disco o un volcado. El modo de prueba de Google puede caducar y la eventual rotación del token de renovación no se guarda automáticamente en Windows. Esta configuración sigue siendo experimental y no está validada para uso real.
