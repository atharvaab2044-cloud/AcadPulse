@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo AcadPulse virtual environment is missing.
  echo Follow INSTALLATION_WINDOWS.md and create .venv first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 (
  echo.
  echo AcadPulse exited with an error. Copy the terminal output when asking for help.
  pause
)
endlocal
