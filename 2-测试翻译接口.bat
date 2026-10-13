@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=F:\Anaconda\Anaconda3\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" test_engines.py
pause
