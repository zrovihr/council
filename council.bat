@echo off
:loop
python "%~dp0council.py" %*
if %ERRORLEVEL% equ 42 goto loop
