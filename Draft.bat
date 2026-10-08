@echo off
REM Draft — double-click me
setlocal
cd /d "%~dp0"
set NEEDLE_TELEMETRY=0
set PYTHONUTF8=1
py -3.13 draft.py
if errorlevel 1 py draft.py
