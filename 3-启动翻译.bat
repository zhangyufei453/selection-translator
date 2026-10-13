@echo off
cd /d "%~dp0"
set "PYW=F:\Anaconda\Anaconda3\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" translator.py
