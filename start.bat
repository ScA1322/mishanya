@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
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
".venv\Scripts\python.exe" -m mishanya
if errorlevel 1 (
    echo.
    echo Mishanya завершилась с ошибкой — причина указана выше и в файле data\mishanya.log
    pause
    exit /b 1
)
exit /b 0
