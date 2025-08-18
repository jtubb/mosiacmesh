@echo off
REM Test runner batch file for Windows
REM This avoids conflicts with Python module imports

echo Running MosaicMesh Test Suite
echo ============================

REM Check if pytest is available
pytest --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing pytest and dependencies...
    python -m pip install pytest pytest-asyncio pytest-cov
    if %errorlevel% neq 0 (
        echo Failed to install pytest. Please install manually:
        echo pip install pytest pytest-asyncio pytest-cov
        pause
        exit /b 1
    )
)

REM Parse command line arguments
set TEST_ARGS=
set RUN_UNIT=0
set RUN_INTEGRATION=0
set RUN_COVERAGE=0
set VERBOSE=0

:parse_args
if "%1"=="--unit" (
    set RUN_UNIT=1
    shift
    goto parse_args
)
if "%1"=="--integration" (
    set RUN_INTEGRATION=1
    shift
    goto parse_args
)
if "%1"=="--coverage" (
    set RUN_COVERAGE=1
    shift
    goto parse_args
)
if "%1"=="-v" (
    set VERBOSE=1
    shift
    goto parse_args
)
if "%1"=="--verbose" (
    set VERBOSE=1
    shift
    goto parse_args
)
if "%1"=="" goto run_tests
set TEST_ARGS=%TEST_ARGS% %1
shift
goto parse_args

:run_tests
REM Build test command
set CMD=pytest

REM Add test paths
if %RUN_UNIT%==1 (
    set CMD=%CMD% tests/unit
) else if %RUN_INTEGRATION%==1 (
    set CMD=%CMD% tests/integration
) else (
    set CMD=%CMD% tests/unit tests/integration
)

REM Add options
if %VERBOSE%==1 set CMD=%CMD% -v
if %RUN_COVERAGE%==1 set CMD=%CMD% --cov=server --cov-report=html --cov-report=term

REM Add configuration
set CMD=%CMD% -c tests/pytest.ini

REM Add any additional arguments
if not "%TEST_ARGS%"=="" set CMD=%CMD% %TEST_ARGS%

echo Running: %CMD%
echo.
%CMD%

if %errorlevel% neq 0 (
    echo.
    echo Tests failed with exit code: %errorlevel%
    pause
    exit /b %errorlevel%
)

if %RUN_COVERAGE%==1 (
    echo.
    echo Coverage report generated in: htmlcov\index.html
)

echo.
echo All tests completed successfully!
pause