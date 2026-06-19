@echo off
chcp 65001 >nul
title 停止 AI Usage Check

echo ========================================
echo   停止 AI Usage Check 服务
echo ========================================
echo.

REM 方式1: 按端口杀掉占用 8080 的进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":8080 " ^| findstr "LISTENING"') do (
    echo 发现进程 PID=%%a 正在监听 8080 端口，正在终止...
    taskkill /PID %%a /F >nul 2>&1
    if errorlevel 1 (
        echo [失败] 无法终止进程 %%a
    ) else (
        echo [成功] 已终止进程 %%a
    )
    goto :done
)

REM 方式2: 按进程名查找 uvicorn/python app.py
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO TABLE /NH ^| findstr /C:"python"') do (
    echo 发现 python.exe 进程，正在终止...
    taskkill /IM python.exe /F >nul 2>&1
)

echo 未发现监听 8080 端口的进程，服务可能已停止。

:done
echo.
pause
