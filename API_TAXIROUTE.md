# API de TaxiRoute: contrato actual de la instalación local

Esta guía describe la API de la composición SQLite con recuperación experimental. Es el contrato utilizado por la interfaz web actual, no una API pública estable. Puede cambiar antes de la entrega final.

La instalación con proxy escucha en `https://127.0.0.1:18771` en el mismo ordenador. Las solicitudes necesitan HTTPS y una sesión válida. Los cambios de estado requieren además una cabecera `Origin` igual al origen de la aplicación y un cuerpo JSON; no se aceptan parámetros en la URL. El servidor comprueba los hosts autorizados. El certificado local es autofirmado: una futura integración deberá disponer de una relación de confianza adecuada.

## Acceso

El acceso de conductor se realiza con `POST /acceso/conductor` y un objeto JSON con las claves `usuario` y `contrasena`. Si la cuenta está habilitada y no necesita cambiar su contraseña, el servidor devuelve la cookie de sesión `taxiroute_sesion`, con los atributos `Secure`, `HttpOnly` y `SameSite=Strict`. El acceso del administrador tiene un flujo separado con código por correo. La sesión identifica la cuenta: no se debe indicar un identificador de conductor para consultar el historial de otra persona.

Las cookies y las contraseñas son privadas. No deben guardarse en ejemplos, repositorios ni registros. Esta forma de autenticación está pensada para la aplicación web y todavía no define un mecanismo estable para clientes externos.

## Operaciones principales

| Método y ruta | Datos JSON | Resultado |
| --- | --- | --- |
| `GET /api/carrera` | No lleva cuerpo | Estado, importe, indicador de carrera activa y revisión actual. |
| `GET /api/carrera/tarifas` | No lleva cuerpo | Tarifas fijadas al iniciar la carrera, si existe una. |
| `POST /api/carrera/iniciar` | `revision`, `id_operacion` | Inicia una carrera en estado parado. |
| `POST /api/carrera/estado` | `revision`, `id_operacion`, `estado` | Cambia el estado a `parado` o `en movimiento`. |
| `POST /api/carrera/finalizar` | `revision` | Prepara el cierre, lo guarda en el historial y libera la carrera. |
| `GET /api/historial` | No lleva cuerpo | Devuelve las carreras de la cuenta autenticada. |

La revisión es un entero que identifica el estado conocido por el cliente. Primero se consulta `GET /api/carrera` y después se envía la revisión recibida. Si otro cambio ha dejado obsoleto ese dato, el servidor rechaza la operación con `409`; el cliente debe consultar de nuevo antes de decidir qué hacer.

En el inicio y los cambios de estado, `id_operacion` es un UUID en formato canónico generado por el cliente. El cliente conserva el mismo identificador para reintentar esa misma operación con la misma acción y revisión; el servidor puede reconocer que ya fue registrada. No debe reutilizarse para otra acción.

**El cierre no dispone aún de la misma garantía de reintento.** Si la respuesta a `finalizar` se pierde o hay un error de almacenamiento, no se debe asumir ni que se guardó ni que falló. Hay que consultar el estado y el historial y, si la aplicación lo solicita, seguir el flujo de revisión.

## Ejemplo de cuerpos

Los siguientes objetos muestran la forma de las solicitudes. La revisión y el UUID son ejemplos: cada cliente debe utilizar la revisión que reciba y generar su propio UUID para cada operación.

```json
{"revision": 0, "id_operacion": "123e4567-e89b-42d3-a456-426614174000"}
```

```json
{"revision": 1, "id_operacion": "123e4567-e89b-42d3-a456-426614174001", "estado": "en movimiento"}
```

```json
{"revision": 2}
```

El primer objeto corresponde a iniciar; el segundo, a cambiar el estado, y el tercero, a finalizar. Cada operación es un `POST` a la ruta indicada en la tabla, con `Content-Type: application/json`, la cookie de sesión y el `Origin` apropiado.

## Errores y recuperación

- `400`: transporte, parámetros o datos de la solicitud no válidos.
- `401`: sesión no disponible, revocada o caducada.
- `403`: origen rechazado.
- `409`: revisión desactualizada, acción incompatible o carrera que necesita revisión.
- `415`: un `POST` no contiene JSON.
- `503`: el almacenamiento no permite confirmar el resultado; se necesita comprobar el estado y puede ser necesaria una revisión.

La composición con recuperación habilitada también ofrece rutas para consultar y confirmar la revisión de una carrera interrumpida. La confirmación utiliza el último punto fiable y no cobra automáticamente el intervalo desconocido. Estas rutas y el protocolo de autenticación deben estabilizarse y documentarse antes de recomendar integraciones externas.
