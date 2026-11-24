@echo off
setlocal
REM One-time setup for Claude Usage Dashboard (Windows).
REM Checks Python + tkinter (installs Python with winget if missing),
REM checks for Claude Code and its sign-in (offers to install it and sign in),
REM and puts a shortcut on the desktop.

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
set "CLAUDE_EXE="
for /f "delims=" %%F in ('where claude.exe 2^>nul') do if not defined CLAUDE_EXE set "CLAUDE_EXE=%%F"
for /f "delims=" %%F in ('where claude.cmd 2^>nul') do if not defined CLAUDE_EXE set "CLAUDE_EXE=%%F"
if not defined CLAUDE_EXE if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE_EXE=%USERPROFILE%\.local\bin\claude.exe"
if defined CLAUDE_EXE goto :signincheck

echo.
echo [!] Claude Code was not found. The dashboard needs it, because it reads
echo     the sign-in that Claude Code saves. The Claude desktop app is not enough.
echo     Setup can install it with Anthropic's official installer
echo     ^(it runs: irm https://claude.ai/install.ps1 ^| iex^).
choice /c YN /n /m "Install Claude Code now? (Y/N) "
if errorlevel 2 goto :claudeskip
echo.
echo Installing Claude Code...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
if exist "%USERPROFILE%\.local\bin\claude.exe" set "CLAUDE_EXE=%USERPROFILE%\.local\bin\claude.exe"
if defined CLAUDE_EXE goto :signincheck
echo.
echo The Claude Code install did not finish. Install it from https://code.claude.com
echo and run Setup.bat again.
goto :shortcut

:claudeskip
echo Skipped. Install Claude Code from https://code.claude.com and sign in before
echo you start the dashboard.
goto :shortcut

:signincheck
if exist "%USERPROFILE%\.claude\.credentials.json" goto :signedin
echo.
echo [!] No Claude Code sign-in found. Sign in with your claude.ai account
echo     ^(Pro, Max, Team or seat-based Enterprise^).
choice /c YN /n /m "Sign in to Claude Code now? A browser window will open. (Y/N) "
if errorlevel 2 goto :signinskip
echo.
echo Sign in in the browser. When Claude Code says you are logged in,
echo type /exit here to come back to Setup.
echo.
call "%CLAUDE_EXE%"
if exist "%USERPROFILE%\.claude\.credentials.json" goto :signedin
echo.
echo Still no sign-in found. Run claude yourself and sign in, then start the dashboard.
goto :shortcut

:signinskip
echo Skipped. Run claude once and sign in before you start the dashboard.
goto :shortcut

:signedin
echo [ok] Claude Code and its sign-in found.

REM ---- 3. Desktop shortcut ----
:shortcut
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
