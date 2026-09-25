@echo off
setlocal

REM ============================================================
REM Konnaxion Capsule Manager - Local Launcher
REM Starts:
REM - Konnaxion Agent on 127.0.0.1:8765
REM - Konnaxion Capsule Manager GUI on 127.0.0.1:8714
REM ============================================================

set "PROJECT_ROOT=%~dp0"
set "PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%"
for %%I in ("%PROJECT_ROOT%..\runtime") do set "DEFAULT_RUNTIME_ROOT=%%~fI"
for %%I in ("%PROJECT_ROOT%..\Konnaxion") do set "DEFAULT_SOURCE_DIR=%%~fI"

if not defined KX_RUNTIME_ROOT set "KX_RUNTIME_ROOT=%DEFAULT_RUNTIME_ROOT%"
if not defined KX_ROOT set "KX_ROOT=%KX_RUNTIME_ROOT%"
if not defined KX_SOURCE_DIR set "KX_SOURCE_DIR=%DEFAULT_SOURCE_DIR%"
if not defined KX_CAPSULE_OUTPUT_DIR set "KX_CAPSULE_OUTPUT_DIR=%KX_ROOT%\capsules"
if not defined KX_CAPSULE_BUILD_JOB_DIR set "KX_CAPSULE_BUILD_JOB_DIR=%KX_ROOT%\manager\build-jobs"
if not defined KX_CAPSULE_BUILD_CONCURRENCY set "KX_CAPSULE_BUILD_CONCURRENCY=1"
if not defined KX_CAPSULE_PUBLIC_KEY_FILE if exist "%KX_ROOT%\signing\kx-demo-ed25519-public.pem" set "KX_CAPSULE_PUBLIC_KEY_FILE=%KX_ROOT%\signing\kx-demo-ed25519-public.pem"

if not exist "%KX_ROOT%" mkdir "%KX_ROOT%"
if not exist "%KX_CAPSULE_OUTPUT_DIR%" mkdir "%KX_CAPSULE_OUTPUT_DIR%"
if not exist "%KX_CAPSULE_BUILD_JOB_DIR%" mkdir "%KX_CAPSULE_BUILD_JOB_DIR%"

set "MANAGER_HOST=127.0.0.1"
set "MANAGER_PORT=8714"
set "AGENT_HOST=127.0.0.1"
set "AGENT_PORT=8765"

cd /d "%PROJECT_ROOT%"

echo.
echo ==========================================
echo Konnaxion Capsule Manager Local Launcher
echo Project: %PROJECT_ROOT%
echo Runtime: %KX_ROOT%
echo Source:  %KX_SOURCE_DIR%
echo Build jobs: %KX_CAPSULE_BUILD_JOB_DIR%
echo Build concurrency: %KX_CAPSULE_BUILD_CONCURRENCY%
if defined KX_CAPSULE_PUBLIC_KEY_FILE echo Capsule public key: %KX_CAPSULE_PUBLIC_KEY_FILE%
echo ==========================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
    echo ERROR: uv was not found in PATH.
    echo Install uv or open this from an environment where uv is available.
    pause
    exit /b 1
)

echo Checking Python/package imports...
uv run python -c "import os; from pathlib import Path; import kx_agent, kx_manager, kx_manager.ui.server; import kx_manager.services.operation_jobs as op; root=Path(os.environ['PROJECT_ROOT']).resolve(); loaded=Path(op.__file__).resolve(); expected=(root/'kx_manager'/'services'/'operation_jobs.py').resolve(); print('imports ok'); print('Manager source:', loaded); assert loaded == expected, f'Stale Manager import: {loaded} != {expected}'"
if errorlevel 1 (
    echo.
    echo ERROR: Import check failed.
    pause
    exit /b 1
)

echo.
echo Starting Konnaxion Agent...
start "Konnaxion Agent" cmd /k "cd /d "%PROJECT_ROOT%" && uv run python -m kx_agent.main run"

echo Waiting for Agent startup...
timeout /t 3 /nobreak >nul

echo.
echo Starting Konnaxion Capsule Manager GUI...
start "Konnaxion Capsule Manager" cmd /k "cd /d "%PROJECT_ROOT%" && uv run python -m kx_manager.main --host %MANAGER_HOST% --port %MANAGER_PORT%"

echo Waiting for Manager startup...
timeout /t 4 /nobreak >nul

echo.
echo Opening GUI...
start "" "http://%MANAGER_HOST%:%MANAGER_PORT%/ui"

echo.
echo ==========================================
echo Started.
echo Agent:   http://%AGENT_HOST%:%AGENT_PORT%/v1/health
echo Manager: http://%MANAGER_HOST%:%MANAGER_PORT%/ui
echo Docs:    http://%MANAGER_HOST%:%MANAGER_PORT%/docs
echo ==========================================
echo.
echo Close the Agent and Manager terminal windows to stop services.
echo.

pause
endlocal