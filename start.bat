@echo off
setlocal
chcp 65001 >nul
echo ========================================
echo    NOVIX One-Click Start
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python not found. Please install Python 3.10+.
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Node.js not found. Please install Node.js 18+.
    echo Download: https://nodejs.org/
    echo.
    pause
    exit /b 1
)

echo [1/3] Starting backend...
start "NOVIX Backend" cmd /k "cd /d %~dp0backend && run.bat"
timeout /t 3 >nul

echo [2/3] Starting frontend...
start "NOVIX Frontend" cmd /k "cd /d %~dp0frontend && run.bat"

echo.
echo [3/3] Services started.
echo.
echo ========================================
echo  URLs:
echo ----------------------------------------
echo  Frontend:  http://novix.dmxy.site
echo  Backend:   http://novix.dmxy.site/api
echo  Docs:      http://novix.dmxy.site/api/docs
echo ========================================
echo.
echo Tip: Close the opened windows to stop services.
echo.
pause
