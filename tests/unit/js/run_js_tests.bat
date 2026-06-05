@echo off
REM Node-based JS unit tests. Requires Node 20+ (built-in --test runner).
REM Run from repo root.
node --test tests/unit/js/*.js
