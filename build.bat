@echo off
REM Rebuild FloatNote into a single self-contained executable.
REM Requires .NET 10 SDK. Output: ..\FloatNote.exe

setlocal
cd /d "%~dp0src"

echo [1/2] publishing...
dotnet publish -c Release -r win-x64 --self-contained true ^
  -p:PublishSingleFile=true ^
  -p:EnableCompressionInSingleFile=true ^
  -p:IncludeNativeLibrariesForSelfExtract=true ^
  -p:DebugType=None -p:DebugSymbols=false ^
  -o "..\publish"
if errorlevel 1 goto fail

echo [2/2] copying...
copy /y "..\publish\FloatNote.exe" "..\FloatNote.exe" >nul
if errorlevel 1 goto fail

del /q "..\publish\*.xml" 2>nul
rd /s /q "..\publish" 2>nul

echo done: "%~dp0..\FloatNote.exe"
exit /b 0

:fail
echo BUILD FAILED
exit /b 1
