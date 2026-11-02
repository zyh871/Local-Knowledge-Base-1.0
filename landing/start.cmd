@echo off
REM  ZhiJing AI - Product Landing Page : start
REM    start.cmd              open index.html in the default browser
REM    start.cmd -Server      serve on http://127.0.0.1:8080 and open it
REM    start.cmd -Server -Port 9000
REM    start.cmd -NoBrowser   serve only (for diagnostics)

setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo  Start failed with code %RC%.
  pause
)
endlocal
exit /b %RC%
