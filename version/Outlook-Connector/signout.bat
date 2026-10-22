@echo off
cd /d "%~dp0"
title PhantomOps - Outlook Sign Out
echo ============================================================
echo   PhantomOps - Outlook Sign Out (disconnect current mailbox)
echo ============================================================
echo.

set "TOKEN=..\outlook_token.txt"

if not exist "%TOKEN%" (
  echo No mailbox is currently connected ^(no outlook_token.txt found^).
  echo To connect one, run  run.bat .
  echo.
  pause
  exit /b 0
)

echo This disconnects the Outlook mailbox PhantomOps is currently using, by
echo deleting its saved sign-in token from the PhantomOps folder.
echo.
set /p CONFIRM="Type Y and press Enter to sign out (anything else cancels): "
if /i not "%CONFIRM%"=="Y" (
  echo.
  echo Cancelled - nothing was changed.
  echo.
  pause
  exit /b 0
)

del /f /q "%TOKEN%" >nul 2>&1
if exist "%TOKEN%" (
  echo.
  echo ERROR: could not delete the token. Make sure PhantomOps ^(start.bat^) is
  echo NOT running, then try again.
  echo.
  pause
  exit /b 1
)

echo.
echo Done - the old mailbox is disconnected.
echo.
set /p NEXT="Connect a NEW mailbox now? Type Y to run the connector: "
if /i "%NEXT%"=="Y" (
  call run.bat
) else (
  echo.
  echo When you are ready, run  run.bat  to connect a new mailbox.
  echo.
  pause
)
