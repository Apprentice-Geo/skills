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

echo uv python install 3.12
echo.
call uv python install 3.12
if %ERRORLEVEL% NEQ 0 goto setup_failed

call uv run --python 3.12 --no-sync python -m scripts.setup.bootstrap %*
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%

:setup_failed
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%
