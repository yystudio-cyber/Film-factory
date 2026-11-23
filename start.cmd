@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
if not exist config.local.json copy config.example.json config.local.json >nul
if not defined FILM_PYTHON set "FILM_PYTHON=python"
"%FILM_PYTHON%" film.py startup-check
if errorlevel 1 exit /b 1
echo Film Factory http://127.0.0.1:8766
"%FILM_PYTHON%" film.py serve
exit /b %errorlevel%
