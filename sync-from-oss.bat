@echo off
:: Sync CODE from the public slugtube-oss repo into this private repo,
:: PRESERVING private files: channels.txt, fix-update.bat, SlugTube-Guide.md,
:: docker-compose.yml (your mounts). Run, review, then git push.
cd /d "%~dp0"
git remote get-url oss >nul 2>&1 || git remote add oss https://github.com/bmcgarybot/slugtube-oss.git
git fetch oss master || (echo Fetch failed & pause & exit /b 1)
git checkout oss/master -- web scripts Dockerfile README.md WALKTHROUGH.md channels.txt.example update.bat rebuild.bat
git add -A
git commit -m "Sync code from slugtube-oss" || echo Nothing new to sync.
echo.
echo Done. Private files (channels.txt, docker-compose.yml, fix-update.bat)
echo were NOT touched. Review with 'git show', then: git push
pause
