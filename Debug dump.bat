@echo off
REM Saves the raw usage reply to dump.json if the bars look wrong
python "%~dp0claude_usage_dashboard.py" --dump
