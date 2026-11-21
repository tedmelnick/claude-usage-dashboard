@echo off
REM Sends a test notification, to check that alerts reach you
python "%~dp0claude_usage_dashboard.py" --test-alert
pause
