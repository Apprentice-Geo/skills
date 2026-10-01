@echo off
rem Resolve the data root before changing cwd or starting uv.
set "SKILL_DATA_ROOT=%AUDIO_TRANSCRIBE_DATA_DIR%"
if not defined SKILL_DATA_ROOT for %%I in ("%~dp0..") do set "SKILL_DATA_ROOT=%%~fI"
:scan_data_args
if "%~1"=="" goto data_args_done
if /i "%~1"=="--data-dir" (
    if "%~2"=="" (
        echo Error: --data-dir requires a path. 1>&2
        exit /b 1
    )
    set "SKILL_DATA_ROOT=%~2"
    shift
    shift
    goto scan_data_args
)
set "SKILL_DATA_ARG=%~1"
if /i "%SKILL_DATA_ARG:~0,11%"=="--data-dir=" set "SKILL_DATA_ROOT=%SKILL_DATA_ARG:~11%"
shift
goto scan_data_args
:data_args_done
for %%I in ("%SKILL_DATA_ROOT%") do set "SKILL_DATA_ROOT=%%~fI"
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%SKILL_DATA_ROOT%\.cache\uv"
exit /b 0
