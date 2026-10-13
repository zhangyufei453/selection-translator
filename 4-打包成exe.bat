@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=F:\Anaconda\Anaconda3\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
"%PY%" -m PyInstaller --noconsole --onefile --name HuaciFanyi translator.py
echo.
echo 完成后 exe 在 dist 文件夹里，可以复制到任意位置双击运行。
pause
