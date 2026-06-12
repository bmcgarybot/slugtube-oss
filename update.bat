@echo off
echo ========================================
echo   SlugTube Updater
echo ========================================
echo.

echo Pulling latest from GitHub...
git pull
if %ERRORLEVEL% neq 0 (
    echo.
    echo   Git pull failed! Trying force reset...
    git fetch origin main
    git reset --hard origin/main
)
echo.

echo Restarting container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh browser with Ctrl+Shift+R
echo ========================================
echo.
echo   Code changes:  instant (live mount)
echo   Dockerfile changes:  run rebuild.bat
echo.
pause
