@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Mishanya - установка
echo === Mishanya: первоначальная установка ===

rem Выбираем Python: сначала версии, для которых есть все готовые пакеты (3.14, 3.13, 3.12), затем любой 3.12+.
set "PY="
where py >nul 2>nul && (
    for %%V in (3.14 3.13 3.12) do (
        if not defined PY (py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V")
    )
    if not defined PY (py -3 -c "import sys" >nul 2>nul && set "PY=py -3")
)
if not defined PY where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 goto oldpython

set "PYVER="
for /f "delims=" %%i in ('%PY% -c "import sys; print(sys.version_info[0] * 100 + sys.version_info[1])"') do set "PYVER=%%i"
echo Используется Python:
%PY% --version
if %PYVER% GEQ 315 (
    echo.
    echo ВНИМАНИЕ: для Python %PYVER:~0,1%.%PYVER:~1% ещё нет готового пакета для голосовых каналов ^(davey^).
    echo Бот будет работать, но музыка — нет. Чтобы включить музыку, установите Python 3.14
    echo с https://www.python.org/downloads/ и снова запустите setup.bat — он сам выберет 3.14.
    echo.
)

rem Окружение от другой версии Python (например, после неудачной установки) создаём заново.
if exist ".venv\Scripts\python.exe" (
    set "VENVVER="
    rem Путь без кавычек в начале: иначе cmd /c внутри for /f срежет кавычки и команда сломается.
    for /f "delims=" %%i in ('.venv\Scripts\python.exe -c "import sys; print(sys.version_info[0] * 100 + sys.version_info[1])" 2^>nul') do set "VENVVER=%%i"
    call :checkvenv
)
if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto fail
)

echo Устанавливаю зависимости Python ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if errorlevel 1 goto fail
call :voice

".venv\Scripts\python.exe" scripts\setup.py %*
if errorlevel 1 goto fail

echo.
echo Установка завершена.
pause
exit /b 0

:checkvenv
if "%VENVVER%"=="%PYVER%" exit /b 0
echo Пересоздаю окружение .venv под выбранную версию Python ...
rmdir /s /q ".venv"
exit /b 0

:voice
rem Пакет для голосовых каналов ставится только готовой сборкой: компиляция требует Rust и часто не удаётся.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements-voice.txt
if errorlevel 1 (
    echo ВНИМАНИЕ: пакет для голосовых каналов не установлен — музыка работать не будет, остальное работает.
    echo Установите Python 3.14 ^(https://www.python.org/downloads/^) и снова запустите setup.bat.
)
exit /b 0

:nopython
echo ОШИБКА: Python не найден.
echo Установите Python 3.14 с https://www.python.org/downloads/
echo При установке отметьте "Add python.exe to PATH". Затем снова запустите setup.bat.
pause
exit /b 1

:oldpython
echo ОШИБКА: нужен Python 3.12 или новее. Сейчас установлен:
%PY% --version
echo Скачайте Python 3.14: https://www.python.org/downloads/
pause
exit /b 1

:fail
echo.
echo Установка не завершена — см. сообщения выше.
pause
exit /b 1
