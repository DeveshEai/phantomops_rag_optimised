@echo off
setlocal enabledelayedexpansion
title PhantomOps Version 4 - Email Agent
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
  echo Run install.bat first, then start.bat.
  echo.
  pause
  exit /b 1
)

REM Which mailbox provider is active — agent_config.yaml (Settings Control
REM Center) overrides .env, same precedence core.config.get_email_provider()
REM uses for the agent itself. Defaults to outlook if the query fails for any
REM reason (e.g. a first-ever run before agent_config.yaml is readable).
REM Query via a plain script file, not an inline `python -c "...;...(...)"`
REM one-liner — cmd.exe's for/f backtick parser is unreliable with multiple
REM embedded quotes/semicolons/parens in a single quoted argument (verified
REM this silently captured zero output before switching to a script file).
REM print_email_provider.py puts the provider on stdout and NOTHING else
REM (config chatter goes to stderr) — for /f assigns on every line it reads,
REM so extra stdout lines would decide the provider by whichever came last.
REM stderr is deliberately NOT sent to nul: if the query fails, its error is
REM the only clue why the wrong mailbox is about to be opened, and silencing
REM it is what makes a mis-detection look inexplicable to whoever is watching.
set "EMAIL_PROVIDER_ACTIVE=outlook"
set "EMAIL_PROVIDER_DETECTED="
set "EMAIL_PROVIDER_OK="
for /f "usebackq delims=" %%P in (`"%VENPY%" print_email_provider.py`) do set "EMAIL_PROVIDER_DETECTED=%%P"

REM Accept ONLY the two known values. An unrecognised-but-non-empty answer has
REM to warn just as loudly as an empty one: an older print_email_provider.py
REM also wrote config chatter to stdout, so this can land on a stray log line
REM rather than a provider, and treating "not empty" as "valid" would send the
REM agent to the wrong mailbox without a word about why.
if /i "%EMAIL_PROVIDER_DETECTED%"=="gmail"   set "EMAIL_PROVIDER_OK=1"
if /i "%EMAIL_PROVIDER_DETECTED%"=="outlook" set "EMAIL_PROVIDER_OK=1"
if defined EMAIL_PROVIDER_OK set "EMAIL_PROVIDER_ACTIVE=%EMAIL_PROVIDER_DETECTED%"

REM One condition per line, no parenthesised block: the value being reported
REM is untrusted text, and a stray ")" inside a block would break the parse.
if not defined EMAIL_PROVIDER_OK echo.
if not defined EMAIL_PROVIDER_OK echo WARNING: could not read email_provider from agent_config.yaml.
if not defined EMAIL_PROVIDER_OK echo Any error explaining why is printed above this line.
if not defined EMAIL_PROVIDER_OK echo Falling back to: %EMAIL_PROVIDER_ACTIVE%
if not defined EMAIL_PROVIDER_OK echo.

REM ---------------------------------------------------------------------------
REM EDITING THE BLOCK BELOW: cmd.exe parses the whole if/else in ONE pass, so
REM every line inside the parentheses must avoid a bare closing paren and a
REM bare redirect character. An unescaped ")" ends the block early and a ">"
REM is read as a redirect EVEN INSIDE A REM COMMENT. Either one silently
REM rewires which branch runs, so the agent opens the wrong mailbox's sign-in
REM no matter what email_provider says. Escape them as ^( ^) ^> instead.
REM ---------------------------------------------------------------------------
if /i "%EMAIL_PROVIDER_ACTIVE%"=="gmail" (
  title PhantomOps Version 4 - Email Agent ^(Gmail^)
  REM First run: no mailbox token yet, so open Google sign-in to create it.
  if not exist "gmail_token.json" (
    echo No Gmail mailbox connected yet - opening sign-in...
    echo ^(Reads GMAIL_CLIENT_ID/_SECRET from .env; a browser window will open.^)
    echo.
    "%VENPY%" "Gmail-Connector\connect_gmail.py"
    if not exist "gmail_token.json" (
      echo.
      echo Mailbox was not connected. See Gmail-Connector\instructions.txt and retry.
      pause
      exit /b 1
    )
    echo.
  )
) else (
  title PhantomOps Version 4 - Email Agent ^(Outlook^)
  REM First run: no mailbox token yet, so open Microsoft sign-in to create it.
  if not exist "outlook_token.txt" (
    echo No Outlook mailbox connected yet - opening sign-in...
    echo ^(Reads OUTLOOK_* from .env; a browser window will open.^)
    echo.
    "%VENPY%" "Outlook-Connector\connect_outlook.py"
    if not exist "outlook_token.txt" (
      echo.
      echo Mailbox was not connected. Fix the OUTLOOK_* values in .env and retry.
      pause
      exit /b 1
    )
    echo.
  )
)

REM Data folders: by default config.py anchors output/ and assets/ to THIS
REM delivery folder (no env needed). To relocate them, set ASSETS_DIR / OUTPUT_DIR
REM in .env to absolute paths - they are honoured because we no longer force them
REM here (a value set in the environment would otherwise win over .env).
REM Only set a fallback if you explicitly export them before launching.

echo ============================================================
echo   PhantomOps AI Email Agent - Version 4 (%EMAIL_PROVIDER_ACTIVE%)
echo   Polling your mailbox. Leave THIS window open.
echo   Stop with Ctrl+C or by closing the window.
echo ============================================================
echo.

"%VENPY%" run_agent.py

echo.
echo [PhantomOps] Agent stopped.
pause
