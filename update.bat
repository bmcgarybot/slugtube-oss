@echo off
echo ========================================
echo   SlugTube Updater
echo ========================================
echo.

cd /d C:\SlugTube-new
echo Pulling latest from GitHub...
git pull
if %ERRORLEVEL% neq 0 (
    echo.
    echo   Git pull failed! Trying force reset...
    git fetch origin main
    git reset --hard origin/main
)
echo.

echo Copying files...
copy /Y "C:\SlugTube-new\web\app.py" "C:\SlugTube\app\web\app.py"
copy /Y "C:\SlugTube-new\web\indexer.py" "C:\SlugTube\app\web\indexer.py"
xcopy "C:\SlugTube-new\web\templates\*" "C:\SlugTube\app\web\templates\" /E /Y
xcopy "C:\SlugTube-new\web\static\*" "C:\SlugTube\app\web\static\" /E /Y
xcopy "C:\SlugTube-new\scripts\*" "C:\SlugTube\app\scripts\" /E /Y
echo.

echo Restarting container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh browser with Ctrl+Shift+R
echo ========================================
pause
