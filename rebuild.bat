@echo off
echo ========================================
echo   SlugTube Full Rebuild
echo ========================================
echo   Only needed when Dockerfile changes
echo   (new system packages, base image, etc.)
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

echo Stopping container...
docker compose down
echo.

echo Rebuilding image (this may take a minute)...
docker compose build --no-cache
echo.

echo Starting container...
docker compose up -d
echo.

REM Clean up old copy directory (no longer needed — code mounts from repo now)
if exist "C:\SlugTube\app" (
    echo.
    echo Cleaning up old C:\SlugTube\app folder...
    echo   This was the old copy of scripts+web. Code now runs from C:\SlugTube-new directly.
    rmdir /s /q "C:\SlugTube\app"
    if not exist "C:\SlugTube\app" (
        echo   Removed successfully.
    ) else (
        echo   Could not remove — you can delete C:\SlugTube\app manually.
    )
)

echo.
echo ========================================
echo   Done! SlugTube rebuilt and running.
echo   Dashboard: http://localhost:5000
echo.
echo   Code:  C:\SlugTube-new  (git repo)
echo   Data:  C:\SlugTube      (config only)
echo ========================================
pause
