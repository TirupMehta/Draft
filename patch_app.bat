@echo off
REM Draft — ships the current draft.py as a live patch (seconds, no rebuild).
REM The installed exe runs patch\draft.py instead of its frozen code.
setlocal
cd /d "%~dp0"

if not exist "draft.py" (
  echo ERROR: draft.py not found here.
  pause
  exit /b 1
)
set APPDIR=%LOCALAPPDATA%\Draft\app
if not exist "%APPDIR%\Draft\Draft.exe" (
  echo ERROR: Draft is not installed. Run install_app.bat first.
  pause
  exit /b 1
)

echo Stopping Draft...
taskkill /F /IM Draft.exe >nul 2>nul
powershell -NoProfile -Command "for ($i=0; $i -lt 10; $i++) { if (-not (Get-Process Draft -ErrorAction SilentlyContinue)) { break }; Start-Sleep 1 }"

mkdir "%APPDIR%\Draft\patch" 2>nul
copy /Y "draft.py" "%APPDIR%\Draft\patch\draft.py"
if errorlevel 1 (
  echo ERROR: patch copy failed.
  pause
  exit /b 1
)

echo Patch live. Relaunching Draft...
start "" "%APPDIR%\Draft\Draft.exe"
echo Done. Check %%LOCALAPPDATA%%\Draft\draft.log for "patch bootstrap".
