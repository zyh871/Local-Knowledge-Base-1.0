@echo off
REM  ZhiJing AI - Product Landing Page : uninstaller launcher

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
endlocal
exit /b %RC%
