@echo off
rem Double-click this file to start the chatbot and open it in your browser.
rem Close this window (or press Ctrl+C) to stop the server.
cd /d "%~dp0"
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
.venv\Scripts\python.exe app.py
pause
