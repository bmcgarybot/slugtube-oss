@echo off
echo ========================================
echo   SlugTube Updater
echo ========================================
echo.
cd /d C:\SlugTube-new
echo Pulling latest code from GitHub...
git pull
echo.
echo Copying app files to Docker mount...
xcopy "C:\SlugTube-new\web" "C:\SlugTube\app\web" /E /Y
xcopy "C:\SlugTube-new\scripts" "C:\SlugTube\app\scripts" /E /Y
echo.
echo Merging channels.txt (preserving your UI-added channels)...
REM Merge: add any lines from git that aren't already in the local file
if exist "C:\SlugTube\channels.txt" (
    REM Backup local channels first
    copy /Y "C:\SlugTube\channels.txt" "C:\SlugTube\channels.txt.bak" >nul
    REM Append new lines from git version that aren't in local
    for /f "usebackq delims=" %%L in ("C:\SlugTube-new\channels.txt") do (
        findstr /x /c:"%%L" "C:\SlugTube\channels.txt" >nul 2>&1
        if errorlevel 1 (
            echo %%L>>"C:\SlugTube\channels.txt"
        )
    )
    echo   Merged! Your UI-added channels are safe.
) else (
    copy /Y "C:\SlugTube-new\channels.txt" "C:\SlugTube\channels.txt"
    echo   Fresh copy (no local file existed).
)
echo.
echo Restarting SlugTube container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh your browser.
echo ========================================
pause
