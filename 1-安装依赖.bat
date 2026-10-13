@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=F:\Anaconda\Anaconda3\python.exe"
if not exist "%PY%" set "PY=python"
echo 使用的 Python：%PY%
echo 正在安装依赖（清华镜像）……
"%PY%" -m pip install pynput pyperclip pystray Pillow requests -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto fail
echo.
echo 安装完成！接下来可以双击 2-测试翻译接口.bat 或 3-启动翻译.bat
pause
exit /b 0
:fail
echo.
echo 安装失败，请把上面的报错截图发给 Claude
pause
exit /b 1
