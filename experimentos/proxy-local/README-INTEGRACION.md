# Integración local experimental

Esta variante Compose está separada del lanzador habitual y solo es apta para
datos ficticios. Publica HTTPS exclusivamente en 127.0.0.1:18771; Gunicorn
escucha en el bucle interno del espacio de red compartido. Nginx comprueba el
certificado del backend con el nombre taxiroute-backend.local y conserva el
Host externo para que lo valide TaxiRoute. El certificado interno debe contener ese nombre DNS y la IP 127.0.0.1;
el certificado externo debe contener la IP 127.0.0.1.

La aplicación utiliza una carpeta de cuentas, historial y tarifas ya creada
mediante las herramientas de prueba; las rutas del Compose se proporcionan
con TAXIROUTE_PROXY_DATOS, TAXIROUTE_CERT_INTERNO, TAXIROUTE_CLAVE_INTERNA,
TAXIROUTE_CERT_EXTERNO y TAXIROUTE_CLAVE_EXTERNA. No se crean ni migran cuentas.
La autorización temporal se busca en /run/taxiroute/gmail_oauth.json. La entrada experimental admite `--conectar-gmail` de forma explícita y verifica permisos y contenedores antes de consultar el perfil. La carga y recarga se han probado con un almacén simulado y con el perfil autorizado en una instalación temporal, sin enviar correo; no se ha validado todavía una instalación definitiva.

No sustituye la demo ni el primer arranque. El proxy recibe su clave externa y solo el certificado público del backend.
Se ejecuta como usuario 101, con raíz de solo lectura y sin capacidades; su configuración se incorpora a la imagen y su base está fijada por digest. La base de Python también tiene digest y las dependencias tienen versiones exactas, pero no hashes; faltan certificados confiables, cierre bajo carga,
recuperación, Gmail o exposición a la red. No usar para cobros o datos reales.

## Prueba reproducible en Windows

Con Docker Desktop abierto y el entorno virtual del proyecto instalado, ejecuta
desde la raíz del repositorio:

```powershell
.venv\Scripts\python.exe experimentos\proxy-local\probar-integracion.py
```

El ensayo crea cuentas, tarifas y dos certificados ficticios en un directorio
temporal. Construye la imagen y levanta un proyecto Compose de nombre
aleatorio; al terminar o fallar retira sus contenedores, red e imagen. Requiere
el puerto 18771 libre. No usa la cuenta OAuth ni los datos de la instalación.

La prueba desde Windows verificó también el usuario y las restricciones del contenedor del proxy, y comprobó Host y Origin inválidos, atributos de la
cookie, dos conductores con carreras simultáneas e historiales separados y
respuesta ante 12 negociaciones TLS incompletas, que se cerraron al vencer el
plazo. HTTPS se verificó con certificados distintos en ambos tramos. No se
probó un navegador, la confianza del dispositivo, Gmail, restauración, caídas,
carga prolongada ni acceso desde otro dispositivo.


Desde Git Bash, con el entorno virtual activo, también se puede ejecutar:

```bash
python experimentos/proxy-local/probar-integracion.py
```

La publicación en localhost limita el acceso desde el ordenador. Otros contenedores que compartan la red Docker del proyecto podrían alcanzar el proxy.


## Primer arranque experimental

`taxiroute.arranque_proxy_experimental.arrancar_proxy` prepara una composición
nueva con rutas y certificados ficticios ya existentes, conserva los IDs de
ambas imágenes en memoria y comprueba HTTPS. Rechaza proyectos con
contenedores existentes y no consulta Gmail. Se ha ensayado con Docker y
datos ficticios. No es todavía el lanzador de entrega: los IDs no se guardan
entre ejecuciones y no hay procedimiento de reinicio o reutilización.


## Prueba de reinicios

Con Docker Desktop y el puerto local 18771 libre, desde la raíz del proyecto:

```bash
python experimentos/proxy-local/probar-reinicios.py
```

El ensayo crea un proyecto temporal con datos ficticios y lo elimina al
terminar. Comprueba la pérdida del contenido temporal del backend y la
respuesta HTTPS tras reiniciar cada servicio. En el entorno probado, reiniciar
solo el backend dejó de responder hasta reiniciar el proxy; esta observación
puede depender del motor Docker. No prueba carreras activas ni el reinicio de
Docker Desktop, y no automatiza la recuperación de la instalación real.


## Prueba local de reutilización y recuperación

Con Docker Desktop abierto, el entorno virtual activo y el puerto 18771 libre:

```bash
python experimentos/proxy-local/probar-reutilizacion.py
```

Se crean datos, cuentas y un archivo de correo ficticios en una instalación
temporal. El ensayo comprueba reapertura, interrupciones de carrera, revisión
del último punto fiable y recuperación del proxy. Sus recursos temporales se
eliminan al terminar.

También se ha probado `--reiniciar-desktop`, que reinicia Docker Desktop
durante una carrera ficticia. Esta opción afecta al servicio Docker del
equipo y aborta si detecta otros contenedores activos; no la uses si alguien
más está utilizando Docker. Los resultados no equivalen a una prueba de
reinicio de Windows ni autorizan datos o credenciales reales.


## Entrada separada de prueba

`python -m taxiroute.iniciar_proxy_local_experimental` prepara una instalación
nueva propia y luego puede reutilizarla; se ha comprobado interactivamente en
Windows con una cuenta ficticia. Por defecto utiliza una carpeta distinta de
la instalación anterior y no importa sus datos. Si encuentra una carpeta
existente sin identidad proxy completa, se detiene y pide revisión.

Esta entrada no consulta Gmail salvo con la opción explícita `--conectar-gmail`, probada con perfil ficticio y con el perfil autorizado en una instalación temporal sin enviar correos. No sustituye `iniciar-taxiroute.cmd` y solo debe utilizarse con datos de prueba. No está validada para el despliegue
definitivo, los permisos de archivos reales ni la red del taxi.


## Composición aislada para una prueba en red privada

`compose.red-ficticia.yaml` prepara un backend y proxy separados para una prueba puntual con datos ficticios. Exige configurar explícitamente `TAXIROUTE_RED_IP`, `TAXIROUTE_RED_DATOS`, `TAXIROUTE_RED_CERT_INTERNO`, `TAXIROUTE_RED_CLAVE_INTERNA`, `TAXIROUTE_RED_CERT_EXTERNO` y `TAXIROUTE_RED_CLAVE_EXTERNA`. La IP debe ser privada y corresponder al equipo; el puerto externo de esta prueba es el 18773. No reutilices la carpeta de la instalación real ni credenciales de Gmail.

Los dos certificados deben ser distintos. El interno necesita el nombre DNS `taxiroute-backend.local` para la verificación del proxy y la IP elegida para la validación del servidor; el externo necesita esa IP para el navegador. Esta configuración no instala confianza en el móvil ni modifica el cortafuegos. Una dirección privada puede ser accesible a otros dispositivos de la misma red.

Con una instalación temporal, cuenta ficticia y certificados autofirmados, se comprobó el acceso desde un móvil conectado a la misma Wi-Fi: entrada de conductor, carrera, cambios de estado, cierre e historial. Se detuvieron después los contenedores de la prueba; la instalación principal permaneció activa. No se comprobó una tablet ni se acredita TLS confiable, aislamiento de la red o preparación para datos reales.

El ensayo requiere una carpeta de datos SQLite ficticia previamente preparada y archivos TLS externos: no es todavía un procedimiento de instalación final. Ante un fallo conserva los datos para revisión y detén solo los contenedores del proyecto de prueba que hayas identificado.
