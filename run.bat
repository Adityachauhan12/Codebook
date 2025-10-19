@echo off
REM =====================================================
REM Book Shelf Tracker - Windows Runner Script (batch)
REM =====================================================

setlocal

REM -- Virtual environment directory
set "VENV_DIR=ocr_venv"

REM -- Activate virtual environment if present
if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo Activating virtual environment...
    call "%VENV_DIR%\Scripts\activate.bat"
) else (
    echo No virtual environment named '%VENV_DIR%' found.
    echo Create one with:
    echo    python -m venv %VENV_DIR%
    echo    %VENV_DIR%\Scripts\activate.bat
    echo    pip install -r requirements.txt
    exit /b 1
)

REM -- Ensure Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found in PATH. Ensure Python is installed and added to PATH.
    exit /b 1
)

REM -- Default environment variables (use existing values if already set)
if defined YOLO_MODEL (
    rem keep existing
) else (
    set "YOLO_MODEL=yolov8n.pt"
)

if defined OUTPUT_DIR (
    rem keep existing
) else (
    set "OUTPUT_DIR=examples"
)

REM -- Video path argument (first CLI arg) or default
if "%~1"=="" (
    set "VIDEO_PATH=examples\sample_video1.mp4"
) else (
    set "VIDEO_PATH=%~1"
)

REM -- Check video file exists
if not exist "%VIDEO_PATH%" (
    echo Video not found: "%VIDEO_PATH%"
    exit /b 1
)

REM -- Run the detector script
echo Running: python detector.py --mode diff --video "%VIDEO_PATH%"
python detector.py --mode diff --video "%VIDEO_PATH%"

if errorlevel 1 (
    echo.
    echo =============================================
    echo        Process finished with errors.
    echo =============================================
    pause
    exit /b 1
)

echo.
echo =============================================
echo       Process Completed Successfully
echo =============================================
echo.

pause
endlocal
