@echo off
chcp 65001 >nul
setlocal
title Склад зеркал - обновление и запуск

echo ========================================
echo   СКЛАД ЗЕРКАЛ - обновление и запуск
echo ========================================
echo.

rem --- Ищем папку проекта ---
cd /d "%~dp0"
if not exist "requirements.txt" (
  if exist "%USERPROFILE%\Sklad\requirements.txt" (
    cd /d "%USERPROFILE%\Sklad"
  ) else if exist "%USERPROFILE%\Desktop\Sklad\requirements.txt" (
    cd /d "%USERPROFILE%\Desktop\Sklad"
  ) else if exist "%USERPROFILE%\Documents\Sklad\requirements.txt" (
    cd /d "%USERPROFILE%\Documents\Sklad"
  )
)

if not exist "requirements.txt" (
  echo [ОШИБКА] Папка проекта не найдена.
  echo Положите этот файл в папку Sklad и запустите оттуда.
  echo.
  pause
  exit /b 1
)

echo Папка проекта: %CD%
echo.

rem --- Обновляем код, если это копия из git ---
set "BRANCH=claude/mirror-warehouse-inventory-6at16b"

if not exist ".git" goto :no_git
git --version >nul 2>&1
if errorlevel 1 goto :no_git

echo Обновляю код из репозитория...
git fetch origin %BRANCH%
if errorlevel 1 (
  echo.
  echo [ВНИМАНИЕ] Не удалось связаться с GitHub. Работаю с тем, что есть.
  echo.
  goto :run
)
git checkout %BRANCH%
git merge --ff-only origin/%BRANCH%
if errorlevel 1 (
  echo.
  echo [ВНИМАНИЕ] Обновить не вышло - в папке есть свои правки.
  echo Работаю с тем, что есть.
  echo.
)
echo Код обновлён.
echo.
goto :run

:no_git
echo [ВНИМАНИЕ] Это не копия из git, обновить код отсюда нельзя.
echo.
echo Если нужна свежая версия - скачайте её заново:
echo   https://github.com/errxr3/Sklad/tree/%BRANCH%
echo   кнопка Code - Download ZIP
echo.
pause

:run
echo Запускаю сайт...
echo.
call start.bat
