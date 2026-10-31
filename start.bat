@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set MISHANYA_LAUNCHER=1
title Mishanya

if not exist ".venv\Scripts\python.exe" (
    echo Mishanya ещё не установлена. Сначала запустите setup.bat.
    pause
    exit /b 1
)
if not exist ".env" (
    echo Нет файла .env. Запустите setup.bat — он создаст его и подскажет, что заполнить.
    pause
    exit /b 1
)

echo Запуск Mishanya. Чтобы остановить, закройте это окно, нажмите Ctrl+C или запустите stop.bat.

:run
if exist ".update-deps" (
    echo Обновляю зависимости Python после обновления ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if not errorlevel 1 del ".update-deps" >nul 2>nul
)
".venv\Scripts\python.exe" -m mishanya
set "CODE=%errorlevel%"
if "%CODE%"=="75" (
    echo.
    echo Установлено обновление — перезапускаю Mishanya ...
    set MISHANYA_RESTARTED=1
    goto run
)
if not "%CODE%"=="0" (
    echo.
    echo Mishanya завершилась с ошибкой — причина указана выше и в файле data\mishanya.log
    pause
    exit /b 1
)
exit /b 0
