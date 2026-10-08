@echo off
REM Draft — installs the app (user-level, no admin needed)
REM Copies dist\Draft to %LOCALAPPDATA%\Draft\app, creates Start Menu +
REM Desktop shortcuts, and wakes Draft with Windows (24/7 in the tray).
setlocal
cd /d "%~dp0"

if not exist "dist\Draft\Draft.exe" (
  echo ERROR: dist\Draft\Draft.exe not found. Run build_exe.bat first.
  pause
  exit /b 1
)

echo Stopping any running Draft...
taskkill /F /IM Draft.exe >nul 2>nul
powershell -NoProfile -Command "for ($i=0; $i -lt 15; $i++) { if (-not (Get-Process Draft -ErrorAction SilentlyContinue)) { break }; Start-Sleep 1 }; if (Get-Process Draft -ErrorAction SilentlyContinue) { exit 1 }"
if not errorlevel 1 goto killok
echo ERROR: Draft.exe is still running and locking files.
echo Quit it from the system tray, then re-run install_app.bat.
pause
exit /b 1
:killok
set APPDIR=%LOCALAPPDATA%\Draft\app
echo Installing to %APPDIR% ...
if exist "%APPDIR%" rmdir /s /q "%APPDIR%"
if not exist "%APPDIR%" goto dirok
echo ERROR: could not remove the old install because files are locked.
echo Reboot and retry.
pause
exit /b 1
:dirok
mkdir "%APPDIR%"
echo Copying app files...
robocopy "dist\Draft" "%APPDIR%\Draft" /E /NFL /NDL /NJH
if errorlevel 8 goto copyfail
if not exist "%APPDIR%\Draft\Draft.exe" goto copyfail
goto copyok
:copyfail
echo ERROR: copy failed. The install is incomplete. Re-run install_app.bat.
pause
exit /b 1
:copyok
if exist "%APPDIR%\patch" rmdir /s /q "%APPDIR%\patch"

echo Creating shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $s = $w.CreateShortcut($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\Draft.lnk'); $s.TargetPath = $env:LOCALAPPDATA + '\Draft\app\Draft\Draft.exe'; $s.WorkingDirectory = $env:LOCALAPPDATA + '\Draft\app\Draft'; $s.Description = 'Draft - fastest local speech to text'; $s.Save()"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $s = $w.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Draft.lnk')); $s.TargetPath = $env:LOCALAPPDATA + '\Draft\app\Draft\Draft.exe'; $s.WorkingDirectory = $env:LOCALAPPDATA + '\Draft\app\Draft'; $s.Description = 'Draft - fastest local speech to text'; $s.Save()"

echo Enabling autostart (Draft wakes with Windows)...
reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Draft /t REG_SZ /d "\"%APPDIR%\Draft\Draft.exe\" --minimized" /f >nul

echo.
echo ============================================
echo  Draft is installed and will start with
echo  Windows. Launching now (tray icon)...
echo  Hold Right Ctrl, speak, release.
echo ============================================
start "" "%APPDIR%\Draft\Draft.exe"
echo (uninstall anytime with uninstall.bat)
