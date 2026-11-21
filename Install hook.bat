@echo off
REM Maintainer, once: make every git commit stamp the release time automatically
cd /d "%~dp0"
python tools\install_hook.py
pause
