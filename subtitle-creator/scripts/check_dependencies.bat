@echo off
setlocal DisableDelayedExpansion
if not "%OS%"=="Windows_NT" exit /b 2
set "ROOT=%~dp0.."
if not exist "%ROOT%\pyproject.toml" exit /b 2
if not exist "%ROOT%\uv.lock" exit /b 2
where uv >nul 2>nul || exit /b 2
if not exist "%ROOT%\.venv\Scripts\python.exe" exit /b 2
rem Expand user arguments only on CALL's second pass to preserve literal carets.
set "SKILL_RUNTIME_HELPER=%~dp0runtime_paths.bat"
set SKILL_INPUT_ARGS=%*
call "%%SKILL_RUNTIME_HELPER%%" %%SKILL_INPUT_ARGS%%
if %ERRORLEVEL% NEQ 0 exit /b 1
pushd "%ROOT%" || exit /b 2
"%ROOT%\.venv\Scripts\python.exe" -m scripts.check_dependencies %SKILL_FORWARD_ARGS% --data-dir "%SKILL_DATA_ROOT_ARG%"
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
