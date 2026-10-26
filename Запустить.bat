@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw farm_server.py
) else (
  python farm_server.py
)
