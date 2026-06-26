@echo off
setlocal

if not defined UV_CACHE_DIR (
    for %%I in ("%~dp0..\..\.cache\uv") do set "UV_CACHE_DIR=%%~fI"
)

if not defined UV_DEFAULT_INDEX (
    set "UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
)

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Error: setup requires uv.
    echo Install uv from https://docs.astral.sh/uv/ and rerun this command.
    exit /b 1
)

pushd "%~dp0..\.." || exit /b 1

echo uv sync --python 3.12 --no-dev
echo.
call uv sync --python 3.12 --no-dev
if %ERRORLEVEL% NEQ 0 goto setup_failed

call uv run --no-sync python "%~dp0setup.py" %*
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%

:setup_failed
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%
