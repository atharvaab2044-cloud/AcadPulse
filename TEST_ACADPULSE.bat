@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo AcadPulse virtual environment is missing. Follow INSTALLATION_WINDOWS.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pytest -q
set RESULT=%ERRORLEVEL%
echo.
if not "%RESULT%"=="0" echo Some tests failed. Copy the complete output when asking for help.
if "%RESULT%"=="0" echo All collected tests passed.
pause
exit /b %RESULT%
