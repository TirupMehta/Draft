@echo off
REM Draft v2 — one-click installer (Windows)
REM Installs everything + pre-downloads speech models (one time).
setlocal
cd /d "%~dp0"

echo ============================================
echo  Draft installer (v2)
echo ============================================

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher 'py' not found. Install Python 3.12+ from python.org
  echo and tick "Add python.exe to PATH", then re-run install.bat.
  pause
  exit /b 1
)

py -3.13 -m pip install --upgrade pip
if errorlevel 1 py -m pip install --upgrade pip

py -3.13 -m pip install -r requirements.txt
if errorlevel 1 py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: pip install failed. Check your internet connection and re-run.
  pause
  exit /b 1
)

echo.
echo Downloading speech models (one time only, ~800 MB for Pro + 17 MB Flash)...
set NEEDLE_TELEMETRY=0
py -3.13 -c "import draft; e=draft.EngineManager('pro'); e.load(print)"
if errorlevel 1 py -c "import draft; e=draft.EngineManager('pro'); e.load(print)"

echo.
echo ============================================
echo  Done! Run Draft with run.bat (or Draft.bat)
echo  Hold Right Ctrl, speak, release. F9 toggles.
echo  Say "scratch that" to discard a take.
echo ============================================
pause
