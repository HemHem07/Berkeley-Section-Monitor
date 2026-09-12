@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python environment not found. Follow the first-time setup in README.md.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.py"
