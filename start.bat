@echo off
chcp 65001 >nul
title AI Usage Check - 仪表盘

set PYTHON=C:\Users\JayGoaler\AppData\Local\Python\pythoncore-3.14-64\python.exe

if not exist "%PYTHON%" (
    echo [错误] 找不到 Python 3.14: %PYTHON%
    pause
    exit /b 1
)

echo ========================================
echo   AI Usage Check - 用量监控仪表盘
echo ========================================
echo.
echo 启动服务: http://localhost:8080
echo 按 Ctrl+C 停止
echo.

cd /d "%~dp0"
"%PYTHON%" app.py

pause
