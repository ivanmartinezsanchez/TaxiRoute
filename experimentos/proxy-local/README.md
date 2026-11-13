# Laboratorio aislado de proxy para TaxiRoute

Prueba experimental con datos y contraseñas ficticias. No utilizar para acceder a una instalación real ni como configuración de producción. Los servicios comparten el espacio de red none y no publican puertos.

Requiere Docker Desktop y conexión para construir la imagen y descargar dependencias. Desde PowerShell situado en esta carpeta, construye la imagen usando explícitamente la raíz del repositorio como contexto:

    $repositorio = (Resolve-Path (Join-Path (Get-Location) "..\..")).Path
    docker build -f (Join-Path (Get-Location) "Dockerfile.laboratorio") -t taxiroute-proxy-lab:local $repositorio

Se usa el archivo .dockerignore del repositorio para filtrar el contexto; revísalo si se añaden archivos sensibles. Gunicorn está fijado a 27.0.0. La imagen base, Nginx y el resto de dependencias no están fijados a un digest ni a versiones exactas, por lo que la construcción no es completamente determinista.

Desde esta carpeta, usa un nombre de proyecto exclusivo, por ejemplo:

    docker compose -p taxiroute-lab-personal up -d
    docker compose -p taxiroute-lab-personal exec -T backend python /laboratorio/prueba.py
    docker compose -p taxiroute-lab-personal down -v

El último comando elimina los contenedores y el volumen de ese proyecto Compose. Usa un nombre nuevo y no lo ejecutes sobre una instalación que contenga datos que quieras conservar.

La prueba conserva el Host externo, rechaza autoridades y orígenes incorrectos, comprueba inicio de sesión y atributos de cookie, dos conductores concurrentes con historiales aislados y doce conexiones TLS incompletas. Los certificados temporales son ficticios y se verifican en ambos tramos; el backend usa puerto 18772 y la autoridad pública simulada es 127.0.0.1:18771.

Limitaciones: usa imagen base, Nginx y dependencias sin fijar por completo ni verificadas para producción; arranca el backend como root; utiliza el mismo certificado y clave de prueba en proxy y backend; el certificado no establece confianza en dispositivos; no prueba Gmail, datos reales, reinicios, restauración, carga sostenida ni acceso LAN. Una limitación de tiempo no impide toda denegación de servicio. No está integrado en el lanzador ni en la entrega y necesita revisión antes de aplicarlo.
