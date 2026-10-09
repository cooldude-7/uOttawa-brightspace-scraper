@echo off
rem The web app. Started at logon by the "Brightspace web" scheduled task.
rem Double-click to run it by hand; output goes to scraper\logs\web.log.
cd /d "%~dp0..\scraper"
if not exist logs mkdir logs
rem The task passes the exact python.exe the installer checked; by hand,
rem whichever python is first on PATH.
set "PY=%~1"
if "%PY%"=="" set "PY=python"
echo ===== started %date% %time% (%PY%) >> logs\web.log
"%PY%" web.py >> logs\web.log 2>&1
