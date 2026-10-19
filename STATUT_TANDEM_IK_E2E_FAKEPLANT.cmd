@echo off
setlocal
cd /d "%~dp0"
call "%~dp0FINALISER_TANDEM_IK_FAKEPLANT.cmd"
exit /b %ERRORLEVEL%
