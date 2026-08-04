@echo off
cd /d "%~dp0"
echo Running daily stock watchlist...
echo Full TW market scan takes about 3-4 hours. Please be patient and do not close this window.
echo.
python -u main.py
echo.
echo Done! Report saved to docs\index.html - open it with your browser.
pause
