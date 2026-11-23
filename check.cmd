@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not defined FILM_PYTHON set "FILM_PYTHON=python"
if exist "evidence\checksums.sha256" (
  "%FILM_PYTHON%" -B film.py verify-install
) else (
  echo Checking source CLI imports and command registration.
  "%FILM_PYTHON%" -B film.py --help
)
exit /b %errorlevel%
