@echo off
chcp 65001 >nul
echo ========================================
echo   Photo & Video Renamer - 打包为 EXE
echo ========================================
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: 检查/安装 PyInstaller
python -m pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [安装] PyInstaller 未安装，正在安装...
    python -m pip install pyinstaller
    if %errorlevel% neq 0 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
)

echo [1/3] 正在打包（可能需要 1-2 分钟）...
echo.
pyinstaller photo_renamer.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败，请查看上方日志
    pause
    exit /b 1
)

echo.
echo [2/3] 复制配置文件...
copy /Y patterns.json "dist\patterns.json" >nul
if %errorlevel% neq 0 (
    echo [错误] 复制 patterns.json 失败
    pause
    exit /b 1
)

:: 清理构建中间文件
echo [3/3] 清理临时文件...
rmdir /S /Q "build" 2>nul
rmdir /S /Q "__pycache__" 2>nul
del /Q "photo_renamer.pyz" 2>nul

echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo   输出目录: dist\
echo   可执行文件: dist\photo_renamer.exe
echo   配置文件:   dist\patterns.json
echo.
echo   使用方法:
echo   1. 将 dist 文件夹中的所有文件复制到目标电脑
echo   2. 将 photo_renamer.exe 放到要整理的照片目录中
echo   3. 双击运行或用命令行: photo_renamer.exe -s . -m preview
echo.
echo   注意: patterns.json 必须和 photo_renamer.exe 在同一目录
echo.
pause
