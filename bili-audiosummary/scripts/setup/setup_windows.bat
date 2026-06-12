@echo off
setlocal

if not defined UV_CACHE_DIR (
    for %%I in ("%~dp0..\..\.cache\uv") do set "UV_CACHE_DIR=%%~fI"
)

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    uv run --python 3.12 --no-project python "%~dp0setup.py" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3.12 -c "import sys" >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        py -3.12 "%~dp0setup.py" %*
        exit /b %ERRORLEVEL%
    )
)

echo Error: setup requires uv or a local Python 3.12 runtime.
echo Install uv from https://docs.astral.sh/uv/ and rerun this command.
exit /b 1
