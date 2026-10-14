@echo off
REM Draft — run regression tests (no new dependencies, stdlib unittest)
setlocal
cd /d "%~dp0"
set NEEDLE_TELEMETRY=0
set PYTHONUTF8=1
py -3.13 -m unittest discover -s tests -v
if errorlevel 1 py -m unittest discover -s tests -v
if errorlevel 1 (
  echo.
  echo TESTS FAILED — see output above.
  pause
  exit /b 1
)
echo.
echo All tests passed.
