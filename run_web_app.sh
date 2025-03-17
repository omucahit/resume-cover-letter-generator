#!/bin/bash

echo "Running Resume and Cover Letter Generator Web Application..."
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed or not in PATH."
    echo "Please install Python 3.7 or higher from https://www.python.org/downloads/"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment."
        exit 1
    fi
fi

# Activate virtual environment and install dependencies
echo "Activating virtual environment..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "Failed to activate virtual environment."
    exit 1
fi

echo "Installing dependencies..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "Failed to install dependencies."
    exit 1
fi

# Run environment check
echo
echo "Running environment check..."
python check_web_environment.py
if [ $? -ne 0 ]; then
    echo "Environment check failed."
    exit 1
fi

# Run the web application
echo
echo "Starting the web application..."
echo
echo "Access the application at http://localhost:5000"
echo "Press Ctrl+C to stop the server"
echo
python app.py

# Deactivate virtual environment
deactivate 