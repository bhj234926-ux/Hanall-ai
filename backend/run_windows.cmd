@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo HANALL AI Catalog Extractor
echo ==========================================

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating Python environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 goto :error
)

echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] Starting HANALL AI...
set "HANALL_LAN_IP="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPConfiguration ^| Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } ^| Select-Object -First 1).IPv4Address.IPAddress"`) do set "HANALL_LAN_IP=%%I"
echo.
echo PC address:    http://127.0.0.1:8000
if defined HANALL_LAN_IP (
  echo Phone address: http://%HANALL_LAN_IP%:8000
) else (
  echo Phone address: Run ipconfig and use http://IPv4-ADDRESS:8000
)
echo Keep this window open while using HANALL AI.
echo If Windows Firewall asks, allow access on Private networks.
echo.
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :end

:error
echo.
echo Setup failed. Keep this window open and check the error above.
pause

:end
endlocal
