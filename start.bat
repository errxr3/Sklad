@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Склад зеркал

echo ========================================
echo   СКЛАД ЗЕРКАЛ - запуск
echo ========================================
echo.

rem --- Ищем Python ---
set "LAUNCHER="
py -3 --version >nul 2>&1 && set "LAUNCHER=py -3"
if not defined LAUNCHER (
  python --version >nul 2>&1 && set "LAUNCHER=python"
)
if not defined LAUNCHER (
  echo [ОШИБКА] Python не найден.
  echo.
  echo Установите его с https://www.python.org/downloads/
  echo При установке обязательно поставьте галочку "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"

rem --- Окружение создаём один раз ---
if not exist "%PY%" (
  echo [1/3] Создаю рабочее окружение. Это делается один раз...
  %LAUNCHER% -m venv .venv
  if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось создать окружение.
    pause
    exit /b 1
  )
)

rem --- Библиотеки ставим, только если их ещё нет ---
"%PY%" -c "import fastapi, sqlalchemy, reportlab, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo [2/3] Устанавливаю библиотеки. Первый раз это занимает пару минут...
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить библиотеки.
    echo Покажите этот текст тому, кто настраивал систему.
    echo.
    pause
    exit /b 1
  )
)

rem --- База, справочник и сотрудники. Повторный запуск ничего не портит ---
echo [3/3] Готовлю базу...
echo.
"%PY%" -m app.cli setup
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Не удалось подготовить базу.
  pause
  exit /b 1
)

echo.
echo ========================================
echo   Сайт открывается: http://localhost:8000
echo.
echo   Вход: Админов Антон, PIN 1234
echo.
echo   Чтобы остановить сайт - закройте это окно
echo   или нажмите Ctrl+C.
echo ========================================
echo.

rem --- Браузер откроется через пару секунд, когда сервер поднимется ---
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start "" http://localhost:8000"

"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

echo.
echo Сайт остановлен.
pause
