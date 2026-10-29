@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "data\mishanya.pid" (
    echo Mishanya не запущена.
    ping -n 3 127.0.0.1 >nul
    exit /b 0
)
set /p PID=<"data\mishanya.pid"
echo Останавливаю Mishanya (PID %PID%) ...
type nul > "data\stop.flag"
for /l %%i in (1,1,15) do (
    if not exist "data\mishanya.pid" goto done
    ping -n 2 127.0.0.1 >nul
)
echo Приложение не ответило за 15 секунд — завершаю принудительно.
taskkill /PID %PID% /F >nul 2>nul
del "data\mishanya.pid" >nul 2>nul
del "data\stop.flag" >nul 2>nul
:done
echo Mishanya остановлена.
ping -n 3 127.0.0.1 >nul
exit /b 0
