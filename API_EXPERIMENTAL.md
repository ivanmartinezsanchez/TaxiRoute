# API web de TaxiRoute: contrato experimental

Esta descripción corresponde a la web integrada en modo durable experimental. La API está pensada actualmente para la interfaz incluida, usa sesión del navegador y funciona en el equipo local. No es todavía un contrato estable para integraciones externas ni un despliegue de producción.

## Acceso y seguridad

Las solicitudes requieren HTTPS. La composición actual admite los hosts `localhost` y `127.0.0.1`. Las peticiones POST deben enviar JSON con `Content-Type: application/json` y una cabecera `Origin` que coincida exactamente con el origen de la aplicación. No se admiten peticiones entre sitios. La cookie de sesión `taxiroute_sesion` es `Secure`, `HttpOnly` y `SameSite=Strict`; no se incluye en ejemplos ni debe copiarse a la documentación o a registros.

El conductor inicia sesión con `POST /acceso/conductor` y los campos `usuario` y `contrasena`. El administrador usa `POST /acceso/administrador` y después `POST /acceso/administrador/pin`; si es obligatorio cambiar la contraseña, el acceso requiere completar también ese paso. `POST /acceso/cerrar` revoca la sesión. Para probar la aplicación sin cuentas reales se debe utilizar la demostración documentada en el README.

Las solicitudes de carrera utilizan la cuenta de la sesión; no aceptan que el cliente elija un conductor mediante un identificador. Las consultas automáticas no prolongan por sí mismas la inactividad de la sesión. Una sesión puede caducar o revocarse mientras se usa la aplicación.

## Carrera del usuario autenticado

| Método y ruta | Función | Cuerpo JSON |
| --- | --- | --- |
| `GET /api/carrera` | Consulta estado, importe y revisión actual. | Ninguno. |
| `GET /api/carrera/tarifas` | Consulta las tarifas fijadas para la carrera en curso. | Ninguno. |
| `POST /api/carrera/iniciar` | Inicia una carrera en parado. | `revision`, `id_operacion`. |
| `POST /api/carrera/estado` | Cambia el estado de la carrera. | `revision`, `id_operacion`, `estado`. |
| `POST /api/carrera/finalizar` | Finaliza y trata de guardar la carrera. | `revision`. |
| `GET /api/historial` | Consulta el historial de la propia cuenta. | Ninguno. |

`revision` es un entero no negativo obtenido del estado más reciente. Para iniciar y cambiar estado, `id_operacion` es un UUID canónico nuevo por acción; si la respuesta se pierde, el mismo identificador y la misma revisión permiten reconocer un reintento ya registrado. En el cambio de estado se acepta `parado` o `en movimiento`.

Ejemplo de cuerpo para un cambio de estado, usando una revisión y un UUID de ejemplo:

```json
{"revision": 1, "id_operacion": "123e4567-e89b-42d3-a456-426614174000", "estado": "en movimiento"}
```

Al terminar, consulta el historial para obtener la carrera guardada. No presupongas que un error de red o almacenamiento significa que la operación no ocurrió: consulta el estado y sigue el procedimiento de revisión si la aplicación lo indica. El cierre no utiliza actualmente el mismo identificador de operación que iniciar y cambiar estado.

## Recuperación y administración

El modo durable incorpora consultas para revisar operaciones y cierres pendientes, confirmar una recuperación y descargar la copia CSV de una carrera pendiente. Son rutas experimentales asociadas a la sesión y no permiten elegir arbitrariamente el propietario. La disponibilidad de recuperación depende de la composición y de que existan puntos verificables.

`GET /api/administracion/historial` requiere sesión administrativa. Acepta `periodo=hoy` o `periodo=todas` y `pagina` entre 1 y 10000; devuelve hasta 100 carreras, indicadores de página anterior y siguiente y el nombre del conductor. El filtro de hoy usa la zona horaria del servidor. El comportamiento y la disponibilidad de los historiales antiguos dependen del almacenamiento activo.

## Respuestas y límites

Los resultados de negocio se devuelven normalmente en JSON. Entre los códigos relevantes están `400` para solicitudes inválidas o transporte no seguro, `401` para acceso o sesión no válidos, `403` para origen o permisos rechazados, `409` para conflictos de estado o revisión, `415` si falta JSON en una ruta que lo requiere, `429` por límite de acceso y `503` si no puede confirmarse una operación o el almacenamiento no está disponible. Las respuestas de error generadas por el servidor no son todas necesariamente JSON.

La interfaz y la API siguen siendo experimentales. Falta definir un mecanismo seguro y un contrato estable para clientes móviles o integraciones distintas del navegador, así como el acceso desde la red del taxi y el despliegue definitivo. El acceso desde ubicaciones distintas permanece aplazado.
