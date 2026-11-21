@echo off
setlocal
REM One-time setup for Claude Usage Dashboard (Windows).
REM Checks Python + tkinter (installs Python with winget if missing),
REM checks for Claude Code, and puts a shortcut on the desktop.

echo.
echo === Claude Usage Dashboard setup ===
echo.

REM ---- 1. Python ----
where python >nul 2>nul
if errorlevel 1 goto :nopython

python -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :notk
echo [ok] Python and tkinter found.
goto :claudecheck

:nopython
echo Python was not found.
where winget >nul 2>nul
if errorlevel 1 (
    echo winget is not available. Install Python 3 from https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" in the installer, then run Setup.bat again.
    goto :end
)
echo Installing Python 3.12 with winget. Accept any prompts that appear.
winget install --id Python.Python.3.12 -e --source winget
if errorlevel 1 (
    echo The Python install did not finish. Install it from https://www.python.org/downloads/ and run Setup.bat again.
    goto :end
)
echo.
echo Python is installed. Close this window and run Setup.bat once more,
echo so Windows picks up the new Python.
goto :end

:notk
echo Python is installed but tkinter is missing.
echo Re-run the python.org installer and choose Modify, then make sure "tcl/tk and IDLE" is ticked.
goto :end

REM ---- 2. Claude Code ----
:claudecheck
set "CLAUDE_OK="
where claude >nul 2>nul && set "CLAUDE_OK=1"
where claude.cmd >nul 2>nul && set "CLAUDE_OK=1"
if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE_OK=1"
if not defined CLAUDE_OK (
    echo [!] Claude Code was not found. Install it from https://code.claude.com
)
if exist "%USERPROFILE%\.claude\.credentials.json" (
    echo [ok] Claude Code sign-in found.
) else (
    echo [!] No Claude Code sign-in found. Run claude once and sign in with your
    echo     claude.ai account ^(Pro, Max, Team or seat-based Enterprise^).
)

REM ---- 3. Desktop shortcut ----
set "PYW="
for /f "delims=" %%P in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
if not exist "%PYW%" (
    echo Could not find pythonw.exe, so no shortcut was made. Use Start Claude Usage.bat instead.
    goto :end
)
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "PYW_EXE=%PYW%"
set "APP_SCRIPT=%APPDIR%\claude_usage_dashboard.py"
set "APP_FOLDER=%APPDIR%"
set "ICON_FILE=%APPDIR%\assets\icon.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Claude Usage Dashboard.lnk')); $s.TargetPath=$env:PYW_EXE; $s.Arguments='\"'+$env:APP_SCRIPT+'\"'; $s.WorkingDirectory=$env:APP_FOLDER; $s.Description='Claude Usage Dashboard'; if (Test-Path $env:ICON_FILE) { $s.IconLocation=$env:ICON_FILE+',0' }; $s.Save()"
if errorlevel 1 (
    echo Could not create the desktop shortcut. Use Start Claude Usage.bat instead.
) else (
    echo [ok] Desktop shortcut created: Claude Usage Dashboard
)

echo.
echo Setup done. Double-click the desktop shortcut to start the dashboard.

:end
echo.
pause
endlocal
