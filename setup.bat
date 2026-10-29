@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Mishanya - установка
echo === Mishanya: первоначальная установка ===

set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 goto oldpython

if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto fail
)

echo Устанавливаю зависимости Python ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if errorlevel 1 goto fail

".venv\Scripts\python.exe" scripts\setup.py %*
if errorlevel 1 goto fail

echo.
echo Установка завершена.
pause
exit /b 0

:nopython
echo ОШИБКА: Python не найден.
echo Установите Python 3.12 или новее с https://www.python.org/downloads/
echo При установке отметьте "Add python.exe to PATH". Затем снова запустите setup.bat.
pause
exit /b 1

:oldpython
echo ОШИБКА: нужен Python 3.12 или новее. Сейчас установлен:
%PY% --version
echo Скачайте новую версию: https://www.python.org/downloads/
pause
exit /b 1

:fail
echo.
echo Установка не завершена — см. сообщения выше.
pause
exit /b 1
