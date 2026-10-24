@echo off
title AI agent operations console
cd /d "%~dp0"
if "%~1"=="" (
  python demo.py
) else (
  python serve_dashboard.py %*
)
if errorlevel 1 echo The server stopped with an error. Read the message above.
pause
