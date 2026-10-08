@echo off
REM Draft v3 — builds dist\Draft\Draft.exe (onedir: fastest startup, no extract)
REM Bundles its own GPU math libs (cublas) so no CUDA Toolkit is needed.
setlocal
cd /d "%~dp0"

set PYBIN=C:\Users\Tirup Mehta\AppData\Local\Programs\Python\Python313\python.exe
set SPKG=C:\Users\Tirup Mehta\AppData\Local\Programs\Python\Python313\Lib\site-packages

"%PYBIN%" -m PyInstaller --noconfirm --clean ^
  --name Draft --onedir --windowed ^
  --icon assets\icon.ico --splash assets\splash.png --version-file assets\version.txt ^
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
