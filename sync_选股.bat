@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title 选股数据同步 - VPS → PC

set VPS_IP=192.236.246.242
set VPS_PASS=53Qgcs517Lh8ETYwUk
set VPS_USER=root
set LOCAL_DIR=G:\Claude Code\选股
set HASH_FILE=%LOCAL_DIR%\_last_hash.txt
set TOOL_DIR=%~dp0tools

:: ====== 确保本地目录存在 ======
if not exist "%LOCAL_DIR%" mkdir "%LOCAL_DIR%"

:: ====== 检查 pscp/plink（PuTTY 工具） ======
if not exist "%TOOL_DIR%" mkdir "%TOOL_DIR%"

set PSCP=pscp.exe
set PLINK=plink.exe
where pscp.exe >nul 2>&1
if %errorlevel% neq 0 (
    if not exist "%TOOL_DIR%\pscp.exe" (
        echo [%date% %time%] ⏬ 首次运行，下载 PuTTY 工具...
        curl -sL --connect-timeout 10 -o "%TOOL_DIR%\pscp.exe" "https://the.earth.li/~sgtatham/putty/latest/w64/pscp.exe"
        curl -sL --connect-timeout 10 -o "%TOOL_DIR%\plink.exe" "https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe"
        if not exist "%TOOL_DIR%\pscp.exe" (
            echo ❌ 自动下载失败，请手动下载：
            echo    https://the.earth.li/~sgtatham/putty/latest/w64/pscp.exe
            echo    https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe
            echo    放到 %TOOL_DIR% 目录
            pause
            exit /b 1
        )
    )
    set PSCP=%TOOL_DIR%\pscp.exe
    set PLINK=%TOOL_DIR%\plink.exe
)

echo ============================================
echo       选股数据同步工具
echo ============================================
echo.

:: ====== 首次运行：接受主机密钥 ======
"%PLINK%" -ssh -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "echo OK" >nul 2>&1
if %errorlevel% neq 0 (
    echo 🔑 首次连接需要接受 VPS 主机密钥
    echo    请输入 "y" 确认（只需一次）
    "%PLINK%" -ssh -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "echo OK"
    echo.
)

:: ====== 获取 VPS 同步标记 ======
echo [%date% %time%] 🔍 检查 VPS 同步标记...
"%PLINK%" -ssh -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP% "cat /tmp/选股_hash.txt 2>/dev/null || echo NOMARK" > "%TEMP%\vps_hash.tmp" 2>&1
set /p VPS_HASH=<"%TEMP%\vps_hash.tmp"

if "%VPS_HASH%"=="NOMARK" (
    echo ⚠ VPS 上无同步标记，将执行完整同步
    set VPS_HASH=FULL_SYNC
)

:: ====== 检查本地同步记录 ======
set LOCAL_HASH=
if exist "%HASH_FILE%" (
    set /p LOCAL_HASH=<"%HASH_FILE%"
)

echo   VPS 标记: %VPS_HASH:~0,20%...
echo   本地标记: %LOCAL_HASH:~0,20%...

if "%VPS_HASH%"=="%LOCAL_HASH%" (
    echo ✅ 已是最新，无需同步
    timeout /t 3 >nul
    exit /b 0
)

:: ====== 开始同步 ======
echo.
echo 🔄 检测到更新，开始同步到 %LOCAL_DIR%
echo.

echo [%date% %time%] 📂 同步 stock_screener...
"%PSCP%" -r -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP%:/opt/openclaw/workspace/选股/stock_screener "%LOCAL_DIR%\" 2>&1
if %errorlevel% neq 0 ( echo ⚠ stock_screener 同步异常 )

echo [%date% %time%] 📂 同步文档...
"%PSCP%" -r -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP%:/opt/openclaw/workspace/选股/*.md "%LOCAL_DIR%\" 2>&1
"%PSCP%" -r -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP%:/opt/openclaw/workspace/选股/bug-reports "%LOCAL_DIR%\bug-reports\" 2>&1

echo [%date% %time%] 📂 同步选股报告...
"%PSCP%" -r -batch -pw %VPS_PASS% %VPS_USER%@%VPS_IP%:/opt/openclaw/workspace/选股/选股报告 "%LOCAL_DIR%\选股报告\" 2>&1

:: ====== 记录本次同步标记 ======
echo %VPS_HASH% > "%HASH_FILE%"

echo.
echo ✅ 同步完成！
echo   时间: %date% %time%
echo   路径: %LOCAL_DIR%
echo.
pause
