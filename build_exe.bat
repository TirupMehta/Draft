@echo off
REM Draft v3 — builds dist\Draft\Draft.exe (onedir: fastest startup, no extract)
REM Bundles its own GPU math libs (cublas) so no CUDA Toolkit is needed.
setlocal
cd /d "%~dp0"

REM Portable Python detection: PYBIN env override wins, else py launcher.
if not defined PYBIN (
  for /f "delims=" %%P in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set PYBIN=%%P
)
if not defined PYBIN (
  for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set PYBIN=%%P
)
if not defined PYBIN set PYBIN=python
if not defined SPKG (
  for /f "delims=" %%P in ('py -3.13 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2^>nul') do set SPKG=%%P
)
if not defined SPKG (
  for /f "delims=" %%P in ('py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2^>nul') do set SPKG=%%P
)
echo Using Python: %PYBIN%
echo Site-packages: %SPKG%

"%PYBIN%" -m PyInstaller --noconfirm --clean ^
  --name Draft --onedir --windowed ^
  --icon assets\icon.ico --splash assets\splash.png --version-file assets\version.txt ^
  --add-data "assets\studio-orb.png;assets" ^
  --collect-all customtkinter ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all tokenizers ^
  --collect-all huggingface_hub ^
  --collect-all onnxruntime ^
  --collect-all av ^
  --collect-all sounddevice ^
  --collect-all soxr ^
  --collect-all pystray ^
  --collect-all PIL ^
  --collect-all keyboard ^
  --collect-all pyperclip ^
  --collect-all needle ^
  --exclude-module torch --exclude-module transformers --exclude-module accelerate ^
  --exclude-module peft --exclude-module datasets --exclude-module gguf ^
  --exclude-module ctranslate2.converters ^
  --exclude-module onnxruntime.transformers --exclude-module onnxruntime.quantization ^
  --exclude-module onnxruntime.tools --exclude-module nvidia ^
  --exclude-module pandas --exclude-module pyarrow ^
  --exclude-module lxml --exclude-module bs4 --exclude-module html5lib --exclude-module soupsieve ^
  --add-binary "%SPKG%\nvidia\cublas\bin\cublas64_12.dll;." ^
  --add-binary "%SPKG%\nvidia\cublas\bin\cublasLt64_12.dll;." ^
  draft.py

if errorlevel 1 (
  echo.
  echo BUILD FAILED — see output above.
  pause
  exit /b 1
)

REM Drop PyInstaller's duplicate CUDA tree (the two DLLs above already
REM sit side-by-side in _internal, which is all the loader needs).
if exist "dist\Draft\_internal\cublas64_12.dll" if exist "dist\Draft\_internal\cublasLt64_12.dll" (
  if exist "dist\Draft\_internal\nvidia" rmdir /s /q "dist\Draft\_internal\nvidia"
)

REM Use the system's NEW VC++ runtime (PyInstaller sometimes grabs an old
REM msvcp140 — old CRT + new CUDA libs = instant crash, see Event ID 1000).
copy /Y C:\Windows\System32\msvcp140.dll "dist\Draft\_internal\msvcp140.dll"
copy /Y C:\Windows\System32\vcruntime140.dll "dist\Draft\_internal\vcruntime140.dll"
copy /Y C:\Windows\System32\vcruntime140_1.dll "dist\Draft\_internal\vcruntime140_1.dll"

echo.
echo Built: dist\Draft\Draft.exe
dir dist\Draft\Draft.exe
