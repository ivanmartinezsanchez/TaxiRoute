# Preparación del correo en la variante con proxy

Esta nota recoge la revisión y el ensayo del 13 de noviembre de 2026. La variante con proxy
es experimental y no debe recibir credenciales de Gmail hasta completar y
comprobar una integración específica. No se ha accedido al almacén ni se ha
enviado correo durante esta revisión.

## Puntos pendientes

- El proxy experimental ahora incorpora la configuración de
  `experimentos/proxy-local/nginx-local-experimental.conf` en su imagen, usa
  una base fijada por digest y se ha probado con usuario 101, raíz de solo
  lectura y capacidades retiradas. No monta la configuración desde el equipo.
  La base fijada no acredita por sí sola la procedencia de la imagen construida;
  aún falta definir una identidad de imagen esperada y verificable.
- `taxiroute/cargar_gmail_windows.py` verifica una composición distinta,
  concreta y con un solo servicio. No debe reutilizarse ni relajarse para esta
  variante. La inspección debe abarcar tanto el backend como el proxy y su
  relación de red.
- El servidor `taxiroute/servidor_proxy_experimental.py` tiene puertos interno
  y externo distintos. Los certificados internos y externos también son
  diferentes. El certificado existente de la instalación, preparado para
  127.0.0.1, no debe reemplazarse silenciosamente.
- Las etiquetas y los identificadores Docker no constituyen una barrera frente
  a un administrador del equipo o del daemon. Tampoco existe una transacción
  que impida un reinicio o sustitución durante toda la secuencia de inspección
  y carga.

## Verificador aislado

Existe `taxiroute/verificador_proxy_experimental.py` para comparar los dos
contenedores con imágenes y rutas esperadas proporcionadas por quien lo llama.
Devuelve su identidad y datos de arranque y puede rechazar cambios respecto a
una comprobación anterior. Se ha comprobado con inspecciones ficticias y con
contenedores Docker temporales; no consulta el almacén ni transmite secretos.
La prueba de compatibilidad real obtuvo los IDs de los propios contenedores,
por lo que no demuestra la cadena de confianza de una construcción controlada.

El verificador es una fotografía y **no autoriza por sí solo la carga de
Gmail**. Aún falta integrar un origen fiable de identidades, verificar los
contenedores en torno a la transferencia con credenciales ficticias y
comprobar la respuesta ante cambios durante el proceso.

## Siguiente diseño a comprobar

El prototipo ya tiene imagen propia del proxy con configuración incorporada,
base identificada por digest, raíz de solo lectura, usuario sin privilegios,
capacidades retiradas y directorio temporal explícito. Falta identificar la
imagen del backend y la del proxy mediante IDs esperados procedentes de una
construcción controlada; una etiqueta por sí sola no acredita su contenido.

Preparar después un verificador independiente que requiera las rutas
esperadas y compruebe, para ambos servicios, identificadores, estado,
imágenes, comandos, entrypoints, entorno, montajes y permisos, publicación
local, tmpfs y opciones de seguridad. También debe comprobar que el proxy
comparte el espacio de red del backend correcto y que solo recibe los
certificados y claves previstos. Cualquier ausencia o discrepancia debe
interrumpir el procedimiento antes de acceder al almacén.

La futura carga debe comprobar nuevamente la identidad y el arranque de los
contenedores y el estado del archivo. Si hay un cambio tras la transmisión, no
se puede asegurar que no se haya expuesto la autorización: es necesario
informar del fallo y revisar el incidente. Este procedimiento no protege frente
a un administrador del sistema o de Docker.

## Pruebas necesarias

- Simular en las inspecciones un proxy ausente, detenido o sustituido y
  cambios en la imagen, el comando, la red, los puertos, los montajes y tmpfs.
  Verificar que no se consulta el almacén ante cada rechazo.
- Simular reinicios y cambios de identidad entre inspecciones, carga y
  verificación posterior. Comprobar que nunca se informa de éxito sin todas
  las comprobaciones.
- Con Docker y datos y autorizaciones ficticios, comprobar los dos
  contenedores, sus identidades, TLS, reinicios y aislamiento de los montajes.
  Comprobar que la configuración y el cargador anteriores conservan su
  funcionamiento.
- Tratar la confianza TLS para móviles y el acceso LAN como una prueba
  posterior, separada de la carga del correo.
