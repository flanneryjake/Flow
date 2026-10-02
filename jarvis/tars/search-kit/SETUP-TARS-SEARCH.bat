@echo off
rem Card #924 - double-click ON TARS (the 5060 laptop). No admin. Writes tars-tools-result.txt next to this file.
cd /d "%~dp0"
python setup_tars_search.py
echo.
echo Done. Result: %~dp0tars-tools-result.txt
pause
