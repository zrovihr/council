@echo off
:loop
python "<user-home>\Tools\council\council.py" %*
if %ERRORLEVEL% equ 42 goto loop
