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
La clave temporal de Gmail se busca en /run/taxiroute/gmail_oauth.json, pero el
cargador existente verifica otro comando y aún no admite esta composición:
el acceso administrativo mediante correo no está integrado ni probado aquí.

No sustituye la demo ni el primer arranque. El proxy recibe su clave externa y solo el certificado público del backend.
Se ejecuta como usuario 101, con raíz de solo lectura y sin capacidades; su configuración se incorpora a la imagen y su base está fijada por digest. No se han fijado
todas las versiones ni revisado certificados confiables, cierre bajo carga,
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
