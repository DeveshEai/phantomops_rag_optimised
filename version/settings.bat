@echo off
setlocal enabledelayedexpansion
title PhantomOps - Settings Control Center
cd /d "%~dp0"

REM Shared environment: the .venv in the PARENT folder, reused by every version
REM placed beside it (same one install.bat created). If this folder ended up
REM nested one level too deep (e.g. a zip was extracted into a folder of the
REM same name), also check a couple of levels further up. Override via PHANTOMOPS_VENV.
if not defined PHANTOMOPS_VENV (
  set "_SV_BASE=%~dp0.."
  for /l %%N in (1,1,3) do (
    for %%I in ("!_SV_BASE!") do set "_SV_CAND=%%~fI\.venv"
    if not defined PHANTOMOPS_VENV if exist "!_SV_CAND!\Scripts\python.exe" set "PHANTOMOPS_VENV=!_SV_CAND!"
    set "_SV_BASE=!_SV_BASE!\.."
  )
  if not defined PHANTOMOPS_VENV for %%I in ("%~dp0..") do set "PHANTOMOPS_VENV=%%~fI\.venv"
)
set "VENPY=!PHANTOMOPS_VENV!\Scripts\python.exe"

if not exist "%VENPY%" (
  echo ERROR: the PhantomOps environment is not installed yet.
  echo Expected: %VENPY%
  echo Run install.bat first, then settings.bat.
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   PhantomOps - Settings Control Center
echo   Opening http://localhost:8502 in your browser...
echo   Default password: sally2026
echo     (change it by setting DASHBOARD_PASSWORD in .env)
echo.
echo   Safe to use WHILE PhantomOps (start.bat) is running - changes
echo   here take effect on the agent's next email (email_provider is
echo   the one exception - that needs a restart, and says so on the
echo   page). Leave THIS window open while you use it; close it to stop.
echo ============================================================
echo.

REM Give the browser a moment to have something to open onto.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8502"

"%VENPY%" -m streamlit run ui\settings_app.py --server.port 8502 --server.headless true

echo.
echo [PhantomOps] Settings Control Center closed.
pause
