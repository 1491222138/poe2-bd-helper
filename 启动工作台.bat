@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   PoE2 Workbench - 一键自检与启动 (Windows)
echo ============================================

set "PY_CMD="
where python >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
    where py >nul 2>&1 && set "PY_CMD=py -3"
)

if not defined PY_CMD (
    echo [提示] 未检测到 Python 3。
    where winget >nul 2>&1
    if %errorlevel%==0 (
        set /p INSTALL_PY="是否通过 winget 自动安装 Python 3.12？[Y/N]: "
        if /i "%INSTALL_PY%"=="Y" (
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            echo 安装完成后请重新双击本脚本启动。
            pause
            exit /b 0
        )
    )
    echo 请先安装 Python 3.10+ (https://www.python.org/downloads/) 或直接下载 GitHub Releases 独立安装包。
    pause
    exit /b 1
)

%PY_CMD% scripts\start_workbench.py %*
if %errorlevel% neq 0 (
    echo.
    echo [退出] 工作台进程已结束（错误码 %errorlevel%）。
    pause
)
endlocal
