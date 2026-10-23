@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw print_farm_sender.py
) else (
  python print_farm_sender.py
)
