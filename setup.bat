```bat
@echo off
cd /d "%~dp0"

echo [1/4] Checking Python...
python --version || (echo Python not found! & pause & exit /b 1)

echo [2/4] Creating .venv...
if not exist ".venv\Scripts\python.exe" python -m venv .venv

echo [3/4] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo [4/4] Installing requirements...
if exist "requirements.txt" (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
) else (
    echo No requirements.txt found - skipped.
)

echo.
echo [SUCCESS] Python environment ready!
echo.
echo Activate: .venv\Scripts\activate
pause
```
