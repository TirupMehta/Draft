@echo off
REM Draft — uninstaller (keeps your models + history in %LOCALAPPDATA%\Draft)
setlocal
echo Stopping Draft...
taskkill /F /IM Draft.exe >nul 2>nul
timeout /t 2 /nobreak >nul
echo Removing autostart...
reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Draft /f >nul 2>nul
echo Removing shortcuts...
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Draft.lnk" >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item ([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Draft.lnk')) -ErrorAction SilentlyContinue"
echo Removing app files (your models/history stay cached)...
if exist "%LOCALAPPDATA%\Draft\app" rmdir /s /q "%LOCALAPPDATA%\Draft\app"
echo.
echo Draft uninstalled. To wipe EVERYTHING including downloaded models,
echo also delete:  %LOCALAPPDATA%\Draft  and  %USERPROFILE%\.cache\huggingface
pause
