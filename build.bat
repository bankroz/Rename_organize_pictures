@echo off
chcp 65001 >nul
echo ========================================
echo   Photo & Video Renamer - 打包为 EXE
echo   (含 TUI 界面 + Textual 环境)
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

:: 检查/安装 Textual
python -m pip show textual >nul 2>&1
if %errorlevel% neq 0 (
    echo [安装] textual 未安装，正在安装...
    python -m pip install "textual>=0.89,<1.0"
    if %errorlevel% neq 0 (
        echo [错误] textual 安装失败
        pause
        exit /b 1
    )
)

echo [1/4] 正在打包（含 TUI，可能需要 2-4 分钟）...
echo.
pyinstaller photo_renamer.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败，请查看上方日志
    pause
    exit /b 1
)

echo.
echo [2/4] 创建 win\ 目录...
if not exist "win" mkdir win

echo [3/4] 复制产物到 win\...
copy /Y "dist\photo_renamer.exe" "win\photo_renamer.exe" >nul
if %errorlevel% neq 0 (
    echo [错误] 复制 exe 失败（exe 是否正在运行？请先关闭）
    pause
    exit /b 1
)
copy /Y "patterns.json" "win\patterns.json" >nul
if %errorlevel% neq 0 (
    echo [错误] 复制 patterns.json 失败
    pause
    exit /b 1
)

:: 清理构建中间文件
echo [4/4] 清理临时文件...
rmdir /S /Q "build" 2>nul
rmdir /S /Q "__pycache__" 2>nul
del /Q "photo_renamer.pyz" 2>nul

echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo   输出目录:    win\
echo   可执行文件:  win\photo_renamer.exe
echo   配置文件:    win\patterns.json
echo.
echo   使用方法:
echo     普通模式: 双击 win\photo_renamer.exe
echo     TUI 模式: 在 cmd 中运行 win\photo_renamer.exe --tui
echo.
echo   注意: patterns.json 必须和 exe 在同一目录
echo.
pause
