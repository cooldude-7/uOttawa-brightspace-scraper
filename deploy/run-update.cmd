@echo off
rem One scrape. Run hourly 07:00-21:00 by the "Brightspace update" task.
rem Double-click to run it by hand; output goes to scraper\logs\update.log.
cd /d "%~dp0..\scraper"
if not exist logs mkdir logs
rem The task passes the exact python.exe the installer checked; by hand,
rem whichever python is first on PATH.
set "PY=%~1"
if "%PY%"=="" set "PY=python"
echo ===== %date% %time% (%PY%) >> logs\update.log
"%PY%" update.py --quiet >> logs\update.log 2>&1
