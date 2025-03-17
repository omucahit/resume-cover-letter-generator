@echo off
echo Running Resume and Cover Letter Generator Web Application...
echo.

REM Check if Python is installed
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Python is not installed or not in PATH.
    echo Please install Python 3.7 or higher from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

REM Activate virtual environment and install dependencies
echo Activating virtual environment...
call venv\Scripts\activate
if %ERRORLEVEL% neq 0 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

REM Run environment check
echo.
echo Running environment check...
python check_web_environment.py
if %ERRORLEVEL% neq 0 (
    echo Environment check failed.
    pause
    exit /b 1
)

REM Run the web application
echo.
echo Starting the web application...
echo.
echo Access the application at http://localhost:5000
echo Press Ctrl+C to stop the server
echo.
python app.py

REM Deactivate virtual environment
call venv\Scripts\deactivate

pause 