@echo off
REM  ZhiJing AI - Product Landing Page : installer launcher
REM  Keep this file ASCII-only. All Chinese UI text lives in install.ps1,
REM  which is packaged as UTF-8 with BOM so PowerShell 5.1 decodes it correctly.

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" echo  Installation failed with code %RC%.
pause
endlocal
exit /b %RC%
