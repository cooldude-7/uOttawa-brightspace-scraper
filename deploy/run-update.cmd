@echo off
rem One scrape. Run hourly 07:00-21:00 by the "Brightspace update" task.
rem Double-click to run it by hand; output goes to scraper\logs\update.log.
cd /d "%~dp0..\scraper"
if not exist logs mkdir logs
echo ===== %date% %time% >> logs\update.log
python update.py --quiet >> logs\update.log 2>&1
