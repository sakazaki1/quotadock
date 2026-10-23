@echo off
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0install_windows.py" %*
) else (
    python "%~dp0install_windows.py" %*
)
if errorlevel 1 pause
