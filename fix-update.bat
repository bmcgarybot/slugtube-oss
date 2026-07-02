@echo off
echo.
echo ============================================
echo   SlugTube Fix - One Click Update
echo ============================================
echo.

REM Step 1: Go to the code folder
cd /d C:\SlugTube-new
if not exist ".git" (
    echo ERROR: C:\SlugTube-new not found. Is SlugTube installed?
    pause
    exit /b 1
)

echo [1/4] Pulling latest code from GitHub...
git fetch origin main
git reset --hard origin/main
echo      Done.
echo.

echo [2/4] Copying ALL web files to container...
xcopy /E /Y /I "web" "C:\SlugTube\app\web" >nul
echo      Done.
echo.

echo [3/4] Copying ALL scripts to container...
xcopy /E /Y /I "scripts" "C:\SlugTube\app\scripts" >nul
echo      Done.
echo.

echo [4/4] Updating channels list...
copy "C:\SlugTube\channels.txt" "C:\SlugTube\channels-backup.txt" >nul 2>&1
copy /Y "channels.txt"                 "C:\SlugTube\channels.txt"
echo      Done.
echo.

echo ============================================
echo   All files updated!
echo.
echo   NOW: Restart the SlugTube container
echo   in Docker Desktop (click the restart
echo   button next to "slugtube")
echo ============================================
echo.
pause
