@echo off
setlocal

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Error: setup requires uv.
    echo Install uv from https://docs.astral.sh/uv/ and rerun this command.
    exit /b 1
)

pushd "%~dp0..\.." || exit /b 1

call uv python install 3.12
if %ERRORLEVEL% NEQ 0 goto setup_failed

call uv sync --python 3.12
if %ERRORLEVEL% NEQ 0 goto setup_failed

popd
exit /b 0

:setup_failed
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%
