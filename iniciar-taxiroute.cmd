@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No se encuentra el entorno virtual del proyecto.
  echo Consulta GUIA_ARRANQUE.md para preparar las dependencias.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m taxiroute.iniciar
pause
