@echo off
REM Node-based JS unit tests. Requires Node 20+ (built-in --test runner).
REM Run from repo root. CMD doesn't expand globs, so iterate explicitly —
REM otherwise `node --test tests/unit/js/*.js` errors with "*.js not found"
REM once there's more than one file in the directory.
setlocal enabledelayedexpansion
set FILES=
for %%f in (tests\unit\js\test_*.js) do set FILES=!FILES! %%f
if "%FILES%"=="" (
  echo No JS test files found at tests\unit\js\test_*.js
  exit /b 0
)
node --test %FILES%
