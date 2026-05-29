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

echo [2/4] Copying web files to container...
copy /Y "web\app.py"                   "C:\SlugTube\app\web\app.py"
copy /Y "web\indexer.py"               "C:\SlugTube\app\web\indexer.py"
copy /Y "web\static\channels.js"       "C:\SlugTube\app\web\static\channels.js"
copy /Y "web\templates\base.html"      "C:\SlugTube\app\web\templates\base.html"
copy /Y "web\templates\channels.html"  "C:\SlugTube\app\web\templates\channels.html"
copy /Y "web\templates\settings.html"  "C:\SlugTube\app\web\templates\settings.html"
copy /Y "web\templates\watch.html"     "C:\SlugTube\app\web\templates\watch.html"
echo      Done.
echo.

echo [3/4] Copying scripts to container...
copy /Y "scripts\download.sh"          "C:\SlugTube\app\scripts\download.sh"
copy /Y "scripts\fetch-banners.sh"     "C:\SlugTube\app\scripts\fetch-banners.sh"
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
