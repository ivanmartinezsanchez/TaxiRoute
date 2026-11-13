@echo off
cd /d "%~dp0"
echo TaxiRoute experimental: solo datos de prueba. Gmail requiere opcion explicita.
if not exist ".venv\Scripts\python.exe" (
  echo No se encuentra el entorno virtual del proyecto.
  echo Consulta la guia de arranque antes de continuar.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m taxiroute.iniciar_proxy_local_experimental %*
if errorlevel 1 (
  echo No se completo el arranque. Conserva la instalacion para revisarla.
  pause
  exit /b 1
)
pause
exit /b 0