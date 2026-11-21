@echo off
REM Maintainer: stamp the release time, run the tests, then commit and push
cd /d "%~dp0"
python tools\release.py %*
pause
