@echo off
setlocal enabledelayedexpansion
title 照片和视频重命名工具 v2.4

:menu
cls
echo ============================================================
echo   照片 ^& 视频批量重命名工具 v2.4
echo ============================================================
echo(
echo   [1] 预览 - 单个文件夹
echo   [2] 预览 - 含所有子文件夹
echo   [3] 执行 - 单个文件夹（直接重命名）
echo   [4] 执行 - 含所有子文件夹（直接重命名）
echo   [5] 自定义参数运行
echo   [6] 整理重复文件名 - 预览
echo   [7] 整理重复文件名 - 执行
echo   [0] 退出
echo(
set /p choice=请选择 (0-7): 

if "%choice%"=="0" exit /b 0
if "%choice%"=="5" goto custom
if "%choice%"=="6" goto ask_dedup_preview
if "%choice%"=="7" goto ask_dedup_execute
if "%choice%"=="1" goto ask_preview_flat
if "%choice%"=="2" goto ask_preview_recursive
if "%choice%"=="3" goto ask_execute_flat
if "%choice%"=="4" goto ask_execute_recursive
goto menu

:ask_folder_common
echo(
echo ------------------------------------------------------------
echo 可以直接拖放文件夹，也可以输入或粘贴路径
echo 直接回车则使用脚本所在目录
echo ------------------------------------------------------------
set /p TARGET=文件夹路径: 

REM 去除拖放产生的引号
set TARGET=%TARGET:"=%

REM 如果为空，使用脚本所在目录
if "%TARGET%"=="" set "TARGET=%~dp0"

REM 去除末尾反斜杠
if "%TARGET:~-1%"=="" set "TARGET=%TARGET:~0,-1%"

REM 验证文件夹是否存在
if not exist "%TARGET%" (
    echo(
    echo [错误] 文件夹不存在: "%TARGET%"
    echo(
    pause
    goto menu
)
exit /b 0

:ask_preview_flat
call :ask_folder_common
echo(
echo [预览] "%TARGET%"（不含子文件夹）
python "%~dp0photo_renamer.py" -s "%TARGET%" -m preview --csv "%TARGET%\preview_report.csv"
pause
goto menu

:ask_preview_recursive
call :ask_folder_common
echo(
echo [预览] "%TARGET%" + 所有子文件夹
python "%~dp0photo_renamer.py" -s "%TARGET%" -m preview -r --csv "%TARGET%\preview_report.csv"
pause
goto menu

:ask_execute_flat
call :ask_folder_common
echo(
echo ============================================================
echo   警告！将对以下路径中的文件直接重命名：
echo   "%TARGET%"
echo   此操作不可撤销！
echo ============================================================
set /p confirm=输入 yes 确认执行: 
if not "%confirm%"=="yes" (
    echo 已取消。
    pause
    goto menu
)
python "%~dp0photo_renamer.py" -s "%TARGET%" -m execute
if errorlevel 2 (
    echo(
    echo ============================================================
    echo   检测到当前目录包含已重命名过的文件
    echo   重新处理可能导致视频文件的时分信息丢失
    echo ============================================================
    set /p force_confirm=如果确认无误，输入 yes 强制重新执行: 
    if "!force_confirm!"=="yes" (
        echo(
        echo 正在强制执行...
        python "%~dp0photo_renamer.py" -s "%TARGET%" -m execute --force
    ) else (
        echo 已取消强制执行。
    )
)
pause
goto menu

:ask_execute_recursive
call :ask_folder_common
echo(
echo ============================================================
echo   警告！将对以下路径及其所有子文件夹重命名：
echo   "%TARGET%"
echo   此操作不可撤销！
echo ============================================================
set /p confirm=输入 yes 确认执行: 
if not "%confirm%"=="yes" (
    echo 已取消。
    pause
    goto menu
)
python "%~dp0photo_renamer.py" -s "%TARGET%" -m execute -r
if errorlevel 2 (
    echo(
    echo ============================================================
    echo   检测到当前目录包含已重命名过的文件
    echo   重新处理可能导致视频文件的时分信息丢失
    echo ============================================================
    set /p force_confirm=如果确认无误，输入 yes 强制重新执行: 
    if "!force_confirm!"=="yes" (
        echo(
        echo 正在强制执行...
        python "%~dp0photo_renamer.py" -s "%TARGET%" -m execute -r --force
    ) else (
        echo 已取消强制执行。
    )
)
pause
goto menu

:ask_dedup_preview
call :ask_folder_common
echo(
echo [副本整理-预览] "%TARGET%"
echo(
python "%~dp0photo_renamer.py" -s "%TARGET%" --dedup -m preview --csv "%TARGET%\dedup_preview.csv"
pause
goto menu

:ask_dedup_execute
call :ask_folder_common
echo(
echo ============================================================
echo   副本整理 - 将对重复命名文件重新分配时间槽：
echo   "%TARGET%"
echo   此操作不可撤销！
echo ============================================================
set /p confirm=输入 yes 确认执行: 
if not "%confirm%"=="yes" (
    echo 已取消。
    pause
    goto menu
)
python "%~dp0photo_renamer.py" -s "%TARGET%" --dedup -m execute --csv "%TARGET%\dedup_log.csv"
pause
goto menu

:custom
echo(
echo ============================================================
echo   自定义参数运行
echo ============================================================
echo(
set /p src=源文件夹路径: 
set /p recursive=包含子文件夹？(y/n): 
set /p mode=模式 (preview/execute): 
set /p fmt=日期格式 (默认=YYYY.MM.DD_HHMM，或自定义): 
set /p outdir=输出目录 (留空=原地重命名): 
set /p csv=CSV报告路径 (留空=自动): 
set /p exts=文件扩展名 (留空=全部，例: .jpg,.mp4): 

set rflag=
if /i "%recursive%"=="y" set rflag=-r

set oflag=
if not "%outdir%"=="" set oflag=-o "%outdir%"

set cflag=
if not "%csv%"=="" set cflag=--csv "%csv%"

set eflag=
if not "%exts%"=="" set eflag=-e "%exts%"

echo(
echo 正在运行...
python "%~dp0photo_renamer.py" -s "%src%" -m %mode% %rflag% -f "%fmt%" %oflag% %cflag% %eflag%
pause
goto menu
