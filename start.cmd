@echo off
setlocal
cd /d "%~dp0"
if not exist config.local.json copy /y config.example.json config.local.json >nul
if not defined FILM_PYTHON set "FILM_PYTHON=D:\ai\venv\Scripts\python.exe"
if not exist "%FILM_PYTHON%" (
  echo Set FILM_PYTHON to your local Python 3.12+ executable.
  pause
  exit /b 1
)
set "PYTHONUTF8=1"
if not defined FILM_LLM_KEY echo Configure FILM_LLM_KEY before starting production.
echo Console: http://127.0.0.1:8766
echo Production monitor: http://127.0.0.1:8766/monitor
echo Screening room: http://127.0.0.1:8766/storyboard
"%FILM_PYTHON%" film.py serve
pause
