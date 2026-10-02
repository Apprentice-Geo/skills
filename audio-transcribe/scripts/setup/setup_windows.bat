@echo off
setlocal DisableDelayedExpansion

rem Expand user arguments only on CALL's second pass to preserve literal carets.
set "SKILL_RUNTIME_HELPER=%~dp0..\runtime_paths.bat"
set SKILL_INPUT_ARGS=%*
call "%%SKILL_RUNTIME_HELPER%%" %%SKILL_INPUT_ARGS%%
if %ERRORLEVEL% NEQ 0 exit /b 1

if not defined UV_INDEX_STRATEGY set "UV_INDEX_STRATEGY=first-index"

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

call uv run --python 3.12 --no-sync python -m scripts.setup.bootstrap %%SKILL_FORWARD_ARGS%% --data-dir "%%SKILL_DATA_ROOT_ARG%%"
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%

:setup_failed
set "SETUP_RC=%ERRORLEVEL%"
popd
exit /b %SETUP_RC%
