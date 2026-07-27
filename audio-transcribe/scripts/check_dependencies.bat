@echo off
setlocal
if not "%OS%"=="Windows_NT" exit /b 2
set "ROOT=%~dp0.."
if not exist "%ROOT%\pyproject.toml" exit /b 2
if not exist "%ROOT%\uv.lock" exit /b 2
where uv >nul 2>nul || exit /b 2
if not exist "%ROOT%\.venv\Scripts\python.exe" exit /b 2
pushd "%ROOT%" || exit /b 2
call "%ROOT%\.venv\Scripts\python.exe" -m scripts.check_dependencies %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
