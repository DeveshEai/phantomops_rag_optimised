@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title PhantomOps - Gmail Mailbox Connector
echo ============================================================
echo   PhantomOps - Gmail Mailbox Connector
echo ============================================================
echo.

REM 1) Prefer PhantomOps's shared virtual environment (the .venv in the parent of
REM    the version folder; this connector lives one level deeper, so go up two).
REM    If this folder ended up nested one level too deep (e.g. a zip was
REM    extracted into a folder of the same name), also check a couple of
REM    levels further up.
set "PY="
if not defined PHANTOMOPS_VENV (
  set "_SV_BASE=%~dp0..\.."
  for /l %%N in (1,1,3) do (
    for %%I in ("!_SV_BASE!") do set "_SV_CAND=%%~fI\.venv"
    if not defined PHANTOMOPS_VENV if exist "!_SV_CAND!\Scripts\python.exe" set "PHANTOMOPS_VENV=!_SV_CAND!"
    set "_SV_BASE=!_SV_BASE!\.."
  )
  if not defined PHANTOMOPS_VENV for %%I in ("%~dp0..\..") do set "PHANTOMOPS_VENV=%%~fI\.venv"
)
if exist "!PHANTOMOPS_VENV!\Scripts\python.exe" set "PY=!PHANTOMOPS_VENV!\Scripts\python.exe"

REM 2) Otherwise use the Windows 'py' launcher, then 'python' on PATH.
if not defined PY (
    where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo ERROR: Python was not found on this PC.
    echo Run install.bat in the PhantomOps folder first, or install Python 3.11
    echo from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
echo (If the Google auth libraries are missing, the connector installs them automatically.)
echo.

"%PY%" connect_gmail.py

echo.
echo (Connector finished. You can close this window.)
pause
