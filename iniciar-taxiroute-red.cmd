@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
echo TaxiRoute en red: Gmail requiere la opcion --conectar-gmail.

if exist ".venv\.taxiroute-preparacion-incompleta" goto preparacion_incompleta
if exist ".venv\Scripts\python.exe" goto iniciar
if exist ".venv" goto entorno_incompleto
if not exist "requirements.txt" goto faltan_requisitos

rem No instalar automaticamente otra version de Python mediante el gestor.
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=0"
where py >nul 2>&1
if errorlevel 1 goto falta_python
call py -3.13 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>&1
if errorlevel 1 goto falta_python

rem Crear solo una carpeta nueva. Si otro inicio la crea antes, no modificarla.
mkdir ".venv" 2>nul
if errorlevel 1 goto entorno_incompleto
> ".venv\.taxiroute-preparacion-incompleta" echo Preparacion pendiente de revision.
if errorlevel 1 goto entorno_incompleto
echo Preparando el entorno de Python. La primera vez puede tardar varios minutos.
call py -3.13 -m venv ".venv"
if errorlevel 1 goto preparacion_incompleta
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto preparacion_incompleta
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto preparacion_incompleta
del ".venv\.taxiroute-preparacion-incompleta"
if exist ".venv\.taxiroute-preparacion-incompleta" goto preparacion_incompleta

:iniciar
".venv\Scripts\python.exe" -m taxiroute.iniciar_red_existente --identidad "%LOCALAPPDATA%\TaxiRoute-LAN\proxy-local" %*
if errorlevel 1 (
  echo No se completo el arranque. Conserva la instalacion para revisarla.
  pause
  exit /b 1
)
pause
exit /b 0

:falta_python
echo No se encuentra Python 3.13 mediante el lanzador py. Comprueba ambos y vuelve a intentarlo.
pause
exit /b 1

:faltan_requisitos
echo Falta requirements.txt. Comprueba que estas usando una copia completa del proyecto.
pause
exit /b 1

:entorno_incompleto
echo La carpeta .venv ya existe pero no contiene un Python utilizable. No se ha modificado.
echo Revisa el entorno antes de continuar.
pause
exit /b 1

:preparacion_incompleta
echo La preparacion del entorno no se completo. Conserva la carpeta .venv para revisarla.
pause
exit /b 1
