@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and ($_.CommandLine -match 'farm_server\.py' -or $_.CommandLine -match 'print_farm_sender\.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 2; $exe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }; if ($exe) { Start-Process -FilePath $exe -ArgumentList 'farm_server.py' -WorkingDirectory (Get-Location) -WindowStyle Hidden } else { Write-Error 'Python not found' }"
endlocal
