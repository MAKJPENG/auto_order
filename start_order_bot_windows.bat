@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    python -m venv .venv
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Could not create .venv. Please install Python 3 first.
  pause
  exit /b 1
)

set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo Installing Python dependencies...
  "%PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Failed to install dependencies. Please check your network and try again.
    pause
    exit /b 1
  )
)

"%PYTHON%" -m playwright install chromium
if errorlevel 1 (
  echo.
  echo Failed to install Chromium for Playwright. Please check your network and try again.
  pause
  exit /b 1
)

"%PYTHON%" -m order_bot.gui
if errorlevel 1 (
  echo.
  echo Program exited with an error.
  pause
)
