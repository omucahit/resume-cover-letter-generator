#!/usr/bin/env python3
"""
Environment checker for Resume and Cover Letter Generator Web Application
This script checks if all required dependencies are installed and configured correctly.
"""

import sys
import os
import importlib.util
import subprocess
from pathlib import Path

def check_python_version():
    """Check if Python version is 3.7 or higher"""
    print(f"Checking Python version...")
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print(f"❌ Python version {version.major}.{version.minor} detected. Version 3.7+ is required.")
        return False
    print(f"✅ Python version {version.major}.{version.minor}.{version.micro} detected.")
    return True

def check_package(package_name):
    """Check if a Python package is installed"""
    print(f"Checking for {package_name}...")
    spec = importlib.util.find_spec(package_name)
    if spec is None:
        print(f"❌ {package_name} is not installed. Install it with: pip install {package_name}")
        return False
    print(f"✅ {package_name} is installed.")
    return True

def check_chrome():
    """Check if Chrome is installed and accessible"""
    print("Checking for Chrome browser...")
    
    # Check common Chrome paths
    chrome_paths = [
        # Windows
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        # macOS
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        # Linux
        '/usr/bin/google-chrome',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium'
    ]
    
    for path in chrome_paths:
        if os.path.exists(path):
            print(f"✅ Chrome found at: {path}")
            return True
    
    # Try to detect Chrome using command
    try:
        if sys.platform.startswith('win'):
            # Windows
            result = subprocess.run(
                ["where", "chrome"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
        else:
            # macOS and Linux
            result = subprocess.run(
                ["which", "google-chrome"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            
        if result.returncode == 0 and result.stdout.strip():
            print(f"✅ Chrome found at: {result.stdout.strip()}")
            return True
    except Exception:
        pass
    
    print("❌ Chrome browser not found.")
    print("   Please install Google Chrome from: https://www.google.com/chrome/")
    print("   Pyppeteer will attempt to download Chromium if Chrome is not available.")
    # Return True anyway since pyppeteer can download Chromium
    return True

def check_openai_api_key():
    """Check if OpenAI API key is set"""
    print("Checking for OpenAI API key...")
    
    # Check if .env file exists
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file, "r") as f:
            content = f.read()
            if "OPENAI_API_KEY" in content and "your_api_key_here" not in content:
                print("✅ OpenAI API key found in .env file.")
                return True
    
    # Check environment variable
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        print("✅ OpenAI API key found in environment variables.")
        return True
    
    print("❌ OpenAI API key not found.")
    print("   Please set your OPENAI_API_KEY in the .env file or as an environment variable.")
    return False

def check_directories():
    """Check if required directories exist"""
    print("Checking required directories...")
    
    required_dirs = ["templates", "static", "uploads", "generated", "temp"]
    missing_dirs = []
    
    for dir_name in required_dirs:
        if not os.path.exists(dir_name):
            missing_dirs.append(dir_name)
    
    if missing_dirs:
        print(f"❌ Missing directories: {', '.join(missing_dirs)}")
        print("   Please create these directories before running the application.")
        return False
    
    print("✅ All required directories exist.")
    return True

def main():
    """Run all checks"""
    print("=" * 60)
    print("Resume and Cover Letter Generator - Web Environment Check")
    print("=" * 60)
    
    checks = [
        check_python_version(),
        check_package("flask"),
        check_package("openai"),
        check_package("PyPDF2"),
        check_package("fitz"),  # PyMuPDF
        check_package("pyppeteer"),
        check_package("dotenv"),
        check_chrome(),
        check_openai_api_key(),
        check_directories()
    ]
    
    print("\nSummary:")
    if all(checks):
        print("✅ All checks passed! Your web environment is ready.")
        print("   Run the application with: python app.py")
    else:
        print("❌ Some checks failed. Please fix the issues above before running the application.")
    
    print("=" * 60)

if __name__ == "__main__":
    main() 