@echo off
echo ========================================
echo   SlugTube Full Rebuild
echo ========================================
echo   Only needed when Dockerfile changes
echo   (new system packages, base image, etc.)
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

echo Stopping container...
docker compose down
echo.

echo Rebuilding image (this may take a minute)...
docker compose build --no-cache
echo.

echo Starting container...
docker compose up -d
echo.
echo ========================================
echo   Done! SlugTube rebuilt and running.
echo   Dashboard: http://localhost:5000
echo ========================================
pause
