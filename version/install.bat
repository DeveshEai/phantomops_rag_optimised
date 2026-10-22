@echo off
setlocal enabledelayedexpansion
title PhantomOps Version 4 - Installer
cd /d "%~dp0"

echo ============================================================
echo   PhantomOps AI Email Agent - Version 4
echo   (Outlook or Gmail . native Windows . no Docker)
echo   INSTALLER
echo ============================================================
echo.

REM ---------------------------------------------------------------
REM 1. Locate Python 3.11+ (py launcher preferred, then python)
REM ---------------------------------------------------------------
set "PYEXE="
for /f "delims=" %%v in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%v"
if not defined PYEXE for /f "delims=" %%v in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%v"
if not defined PYEXE for /f "delims=" %%v in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%v"

if not defined PYEXE (
  echo [Python] Not found. Attempting to install Python 3.11 via winget...
  where winget >nul 2>&1
  if errorlevel 1 (
    echo.
    echo   ERROR: Python is not installed and winget is unavailable.
    echo   Install Python 3.11 from https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH" during setup, then re-run install.bat
    echo.
    pause
    exit /b 1
  )
  winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
  echo.
  echo   Python was installed. CLOSE this window and run install.bat AGAIN
  echo   so the updated PATH takes effect.
  echo.
  pause
  exit /b 0
)

echo [Python] Using: !PYEXE!
for /f "delims=" %%v in ('"!PYEXE!" --version') do echo [Python] %%v
echo.

REM ---------------------------------------------------------------
REM 2. Shared virtual environment: ONE .venv in the PARENT folder, reused by
REM    every version placed beside it. E.g. with
REM        <parent>\.venv  <parent>\version_4  <parent>\version_5
REM    all versions share <parent>\.venv. Re-running install.bat for another
REM    version reuses it and only tops up missing libraries.
REM    If this folder ended up nested one level too deep (e.g. a zip was
REM    extracted into a folder of the same name), also check a couple of
REM    levels further up so an existing .venv there is reused instead of
REM    creating a duplicate. Override by setting PHANTOMOPS_VENV before running.
REM ---------------------------------------------------------------
if not defined PHANTOMOPS_VENV (
  set "_SV_BASE=%~dp0.."
  for /l %%N in (1,1,3) do (
    for %%I in ("!_SV_BASE!") do set "_SV_CAND=%%~fI\.venv"
    if not defined PHANTOMOPS_VENV if exist "!_SV_CAND!\Scripts\python.exe" set "PHANTOMOPS_VENV=!_SV_CAND!"
    set "_SV_BASE=!_SV_BASE!\.."
  )
  if not defined PHANTOMOPS_VENV for %%I in ("%~dp0..") do set "PHANTOMOPS_VENV=%%~fI\.venv"
)
if exist "!PHANTOMOPS_VENV!\Scripts\python.exe" (
  echo [venv] Reusing shared environment: !PHANTOMOPS_VENV!
) else (
  echo [venv] Creating shared environment: !PHANTOMOPS_VENV!
  "!PYEXE!" -m venv "!PHANTOMOPS_VENV!"
  if errorlevel 1 (
    echo   ERROR: failed to create the virtual environment.
    pause
    exit /b 1
  )
)
set "VENPY=!PHANTOMOPS_VENV!\Scripts\python.exe"

REM ---------------------------------------------------------------
REM 2b. Shared database folder (SQLite history + the RAG vector store): same
REM     convention as the shared .venv above - ONE "database" folder in the
REM     PARENT folder, reused by every version placed beside it, so a new
REM     release (version_5 replacing version_4) connects to the SAME task
REM     history and knowledge base instead of starting over empty. Mirrors
REM     core/config.py's _find_shared_dir() so the installer and the running
REM     agent always agree on the same location. Override by setting
REM     DATABASE_DIR before running (also honoured at runtime).
REM ---------------------------------------------------------------
if not defined DATABASE_DIR (
  set "_DB_BASE=%~dp0.."
  for /l %%N in (1,1,3) do (
    for %%I in ("!_DB_BASE!") do set "_DB_CAND=%%~fI\database"
    if not defined DATABASE_DIR if exist "!_DB_CAND!" set "DATABASE_DIR=!_DB_CAND!"
    set "_DB_BASE=!_DB_BASE!\.."
  )
  if not defined DATABASE_DIR for %%I in ("%~dp0..") do set "DATABASE_DIR=%%~fI\database"
)
if exist "!DATABASE_DIR!" (
  echo [DB] Reusing shared database folder: !DATABASE_DIR!
) else (
  echo [DB] Preparing shared database folder: !DATABASE_DIR!
  mkdir "!DATABASE_DIR!"
)
REM chroma_db itself is normally created lazily by the Python chromadb client
REM on first real RAG use (ingest/retrieve) - pre-create it here too so it's
REM visible right after install instead of looking like RAG never got set up.
if not exist "!DATABASE_DIR!\chroma_db" mkdir "!DATABASE_DIR!\chroma_db"

REM ---------------------------------------------------------------
REM 3. Install / top up Python dependencies (online). pip is idempotent:
REM    it installs anything missing and leaves satisfied packages alone,
REM    so this also adds libraries a newer version introduced.
REM ---------------------------------------------------------------
echo.
echo [deps] Upgrading pip ...
"!VENPY!" -m pip install --upgrade pip
echo [deps] Installing/updating requirements - first run can take a few minutes ...
"!VENPY!" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   ERROR: dependency install failed. Check your internet connection and retry.
  pause
  exit /b 1
)

REM ---------------------------------------------------------------
REM 4. Optional helpers: Tesseract OCR + LibreOffice (PDF output)
REM    The agent runs without them, but PDF output / scanned-PDF OCR
REM    need LibreOffice / Tesseract respectively.
REM ---------------------------------------------------------------
echo.
where winget >nul 2>&1
if not errorlevel 1 (
  where tesseract >nul 2>&1
  if errorlevel 1 (
    echo [OCR] Installing Tesseract OCR via winget ...
    winget install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements
  ) else (
    echo [OCR] Tesseract already present.
  )

  REM Arabic language data - the default Tesseract install only ships English.
  REM Download the Arabic traineddata separately so scanned Arabic documents
  REM can be OCR'd. Best-effort: if this fails (no internet, moved file),
  REM OCR just stays English-only - tools/file_reader.py's _ocr_lang() detects
  REM what's actually present and never errors on a missing language pack.
  set "_TESSDATA_DIR="
  if exist "C:\Program Files\Tesseract-OCR\tessdata" set "_TESSDATA_DIR=C:\Program Files\Tesseract-OCR\tessdata"
  if not defined _TESSDATA_DIR if exist "C:\Program Files (x86)\Tesseract-OCR\tessdata" set "_TESSDATA_DIR=C:\Program Files (x86)\Tesseract-OCR\tessdata"
  if defined _TESSDATA_DIR (
    if exist "!_TESSDATA_DIR!\ara.traineddata" (
      echo [OCR] Arabic language data already present.
    ) else (
      echo [OCR] Downloading Arabic language data for Tesseract ...
      curl -fsSL -o "!_TESSDATA_DIR!\ara.traineddata" "https://github.com/tesseract-ocr/tessdata/raw/main/ara.traineddata"
      if errorlevel 1 (
        del "!_TESSDATA_DIR!\ara.traineddata" >nul 2>&1
        echo   [OCR] Could not download Arabic language data automatically.
        echo   Download ara.traineddata from https://github.com/tesseract-ocr/tessdata
        echo   and place it in "!_TESSDATA_DIR!" to enable Arabic OCR.
      ) else (
        echo [OCR] Arabic language data installed.
      )
    )
  ) else (
    echo [OCR] Could not locate the Tesseract tessdata folder - skipping Arabic language data.
  )

  if exist "C:\Program Files\LibreOffice\program\soffice.exe" (
    echo [PDF] LibreOffice already present.
  ) else (
    echo [PDF] Installing LibreOffice via winget - used for PDF output ...
    winget install --id TheDocumentFoundation.LibreOffice -e --silent --accept-package-agreements --accept-source-agreements
  )
) else (
  echo [optional] winget not found - skipping Tesseract and LibreOffice.
  echo            PDF output and scanned-PDF OCR will be limited until you
  echo            install them manually.
)

REM ---------------------------------------------------------------
REM 5. Optional: Ollama (local LLM/embeddings). Powers agent_model: local
REM    and the RAG knowledge base (tools/rag_store.py). The agent runs fine
REM    without it - those two features just stay inactive until it's set up.
REM ---------------------------------------------------------------
echo.
set "OLLAMA_EXE="
where ollama >nul 2>&1
if not errorlevel 1 for /f "delims=" %%v in ('where ollama') do if not defined OLLAMA_EXE set "OLLAMA_EXE=%%v"
if not defined OLLAMA_EXE if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if defined OLLAMA_EXE (
  echo [Ollama] Already installed: !OLLAMA_EXE!
) else (
  where winget >nul 2>&1
  if not errorlevel 1 (
    echo [Ollama] Installing Ollama via winget - used for local models and the RAG knowledge base ...
    winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
      echo   [Ollama] winget install FAILED (exit code !errorlevel!). Common causes:
      echo     - this window isn't running as Administrator ^(right-click install.bat -^>
      echo       "Run as administrator" and try again^)
      echo     - a one-time winget source/license prompt is blocking silently on first ever use
      echo     - no internet connection
      echo   You can also install manually from https://ollama.com/download
    ) else (
      echo [Ollama] winget install completed.
    )
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
      set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    ) else (
      echo   [Ollama] Installer ran but ollama.exe was not found at the expected path
      echo   ^(%LOCALAPPDATA%\Programs\Ollama\ollama.exe^) - it may have installed elsewhere.
      echo   Close this window, reopen a new one ^(so PATH refreshes^), and re-run install.bat.
    )
  ) else (
    echo [optional] winget not found - skipping Ollama.
    echo            Install manually from https://ollama.com/download if you want
    echo            local models (agent_model: local) or the RAG knowledge base.
  )
)

if defined OLLAMA_EXE (
  echo [Ollama] Pulling the RAG embedding model (bge-m3, ~1.2 GB) - this can take a few minutes ...
  REM A just-installed Ollama can take a few seconds for its background service to
  REM come up; retry a few times with a short wait instead of failing on the very
  REM first attempt (this was silently giving up on a fresh install before).
  set "_OLLAMA_PULL_OK="
  for /l %%A in (1,1,3) do (
    if not defined _OLLAMA_PULL_OK (
      "!OLLAMA_EXE!" pull bge-m3
      if not errorlevel 1 (
        set "_OLLAMA_PULL_OK=1"
      ) else (
        echo   [Ollama] Pull attempt %%A/3 failed - Ollama's service may still be starting up. Retrying in 10s...
        timeout /t 10 /nobreak >nul
      )
    )
  )
  if defined _OLLAMA_PULL_OK (
    echo [Ollama] bge-m3 ready.
  ) else (
    echo   [Ollama] Could not pull bge-m3 after 3 attempts. Run "ollama pull bge-m3"
    echo   manually once Ollama is confirmed running ^(check for its tray icon, or run
    echo   "ollama list" in a new terminal^) to enable the RAG knowledge base.
  )
  echo.
  echo [Ollama] A chat model for agent_model: local is NOT pulled automatically -
  echo          pick one yourself, e.g.  ollama pull qwen2.5:14b-instruct
  echo          then set provider_models.local in agent_config.yaml to match.
)

REM ---------------------------------------------------------------
REM 6. Create data folders inside this delivery folder
REM    (chroma_db is handled separately above - step 2b - since it's shared
REM    across versions in the parent folder, not local to this one)
REM ---------------------------------------------------------------
echo.
echo [folders] Preparing data folders ...
for %%D in (output output\inbox output\intake output\ppts output\proposals ^
            assets\templates assets\letterhead assets\logo assets\images) do (
  if not exist "%%D" mkdir "%%D"
)

echo.
echo ============================================================
echo   INSTALLATION COMPLETE
echo   Shared environment: !PHANTOMOPS_VENV!
echo   (other PhantomOps versions on this PC reuse it - no re-download)
echo.
echo   NEXT STEPS
echo   1) Pick a mailbox: agent_config.yaml's email_provider is outlook or
echo      gmail. Easiest via the Settings Control Center - double-click
echo      settings.bat, it opens at http://localhost:8502 (password:
echo      sally2026, changeable via DASHBOARD_PASSWORD in .env). Or edit
echo      agent_config.yaml directly. Then fill in that provider's
echo      credentials in .env:
echo        Outlook: OUTLOOK_CLIENT_ID / _SECRET / _TENANT_ID
echo        Gmail:   GMAIL_CLIENT_ID / _SECRET - see
echo                 Gmail-Connector\instructions.txt for the one-time setup
echo      Also confirm the allowlist (settings.bat or agent_config.yaml).
echo   2) Double-click start.bat - first run opens the matching sign-in
echo      automatically (Outlook-Connector or Gmail-Connector) to create
echo      the mailbox token.
echo   3) Optional - local models / RAG knowledge base: if Ollama was
echo      installed above, drop files in output\intake or email "save this
echo      to RAG" to start building the knowledge base. To reply using a
echo      local model instead of a cloud one, pull a chat model
echo      (ollama pull ^<model^>) and set agent_model: local in
echo      agent_config.yaml.
echo ============================================================
echo.
pause
