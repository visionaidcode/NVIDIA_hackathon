@echo off
rem Starts Meeting Screen Assistant from source with the project's Python.
rem Use this where Windows blocks the unsigned MeetingAssistant.exe (Smart App Control).
rem Extra arguments are passed on, e.g.  MeetingAssistant.bat --stub
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Set it up first, see README.md:
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -e .
    pause
    exit /b 1
)
title Meeting Screen Assistant
".venv\Scripts\python.exe" -m app.main %*
