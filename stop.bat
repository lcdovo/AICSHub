@echo off
setlocal
rem Stop existing AICSHub containers without rebuilding or changing configuration.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\runtime.ps1" -Action stop
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
