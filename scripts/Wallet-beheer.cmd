@echo off
setlocal EnableExtensions
title Wallet-beheer
mode con: cols=72 lines=12 >nul 2>&1

rem One-click owner dashboard. Seeds stay in a visible terminal.
rem python -m wallet_mcp launch reloads only WALLET_MCP_*_ALLOW_MAINNET.

set "PY=%WALLET_BEHEER_PYTHON%"
if defined PY goto :check

call :pick "%~dp0."
if defined PY goto :check
call :pick "%~dp0.."
if defined PY goto :check
call :pick "%~dp0..\.."
if defined PY goto :check
call :pick "%USERPROFILE%\Btc-wallet-MCP"
if defined PY goto :check

echo Python-venv niet gevonden.
echo Verwacht: %USERPROFILE%\Btc-wallet-MCP\.venv\Scripts\python.exe
echo Of stel WALLET_BEHEER_PYTHON in op het pad van python.exe.
pause
exit /b 1

:pick
if exist "%~1\.venv\Scripts\python.exe" set "PY=%~f1\.venv\Scripts\python.exe"
exit /b 0

:check
if not exist "%PY%" (
  echo Python-venv niet gevonden.
  pause
  exit /b 1
)

"%PY%" -m wallet_mcp launch %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
