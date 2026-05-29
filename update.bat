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
echo Updating channels.txt...
if exist "C:\SlugTube\channels.txt" (
    copy /Y "C:\SlugTube\channels.txt" "C:\SlugTube\channels.txt.bak" >nul
    echo   Backed up to channels.txt.bak
)
copy /Y "C:\SlugTube-new\channels.txt" "C:\SlugTube\channels.txt" >nul
echo   Updated channels.txt from repo
echo.
echo Restarting SlugTube container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh your browser (Ctrl+Shift+R)
echo ========================================
echo.
pause
