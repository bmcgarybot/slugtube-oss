@echo off
echo ========================================
echo   SlugTube Updater
echo ========================================
echo.
cd /d C:\SlugTube-new
echo Pulling latest code from GitHub...
git pull
echo.
echo Copying files to Docker mount...
xcopy "C:\SlugTube-new\web" "C:\SlugTube\app\web" /E /Y
xcopy "C:\SlugTube-new\scripts" "C:\SlugTube\app\scripts" /E /Y
copy /Y "C:\SlugTube-new\channels.txt" "C:\SlugTube\channels.txt"
echo.
echo Restarting SlugTube container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh your browser.
echo ========================================
pause
