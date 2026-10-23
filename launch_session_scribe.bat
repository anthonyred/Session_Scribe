@echo off
title Session Scribe
echo Starting Session Scribe from shared venv...
cd /d "D:\Session_Scribe"
call "D:\Session_Scribe\.venv\Scripts\activate.bat"
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
