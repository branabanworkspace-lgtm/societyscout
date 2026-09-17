@echo off
cd /d "%~dp0"
title SocietyScout
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
    echo Setting up SocietyScout for the first time. This takes a minute or two...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python is not installed. Get it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during the install. Then run this again.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Could not install the parts SocietyScout needs. Check your internet connection and try again.
    pause
    exit /b 1
)
echo.
echo SocietyScout is starting and will open in your browser.
echo Keep this window open while you use it. Close it to stop SocietyScout.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app.py
pause
