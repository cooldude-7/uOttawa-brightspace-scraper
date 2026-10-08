@echo off
rem The web app. Started at logon by the "Brightspace web" scheduled task.
rem Double-click to run it by hand; output goes to scraper\logs\web.log.
cd /d "%~dp0..\scraper"
if not exist logs mkdir logs
echo ===== started %date% %time% >> logs\web.log
python web.py >> logs\web.log 2>&1
