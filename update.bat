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
echo Merging channels.txt...

REM Check if Python is available
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo   Python not found - copying channels.txt directly.
    if exist "C:\SlugTube\channels.txt" (
        copy /Y "C:\SlugTube\channels.txt" "C:\SlugTube\channels.txt.bak" >nul
        echo   Backed up existing channels.txt
    )
    copy /Y "C:\SlugTube-new\channels.txt" "C:\SlugTube\channels.txt" >nul
    echo   Copied channels.txt
    goto :restart
)

python -c "import os, shutil; git_file=r'C:\SlugTube-new\channels.txt'; local_file=r'C:\SlugTube\channels.txt'; backup_file=r'C:\SlugTube\channels.txt.bak'; exec(chr(10).join(['if not os.path.exists(local_file):','    shutil.copy2(git_file, local_file)','    print(\"  Fresh copy (no local file existed).\")','else:','    shutil.copy2(local_file, backup_file)','    local_set = set(open(local_file, encoding=\"utf-8\", errors=\"replace\").read().splitlines())','    git_lines = [l for l in open(git_file, encoding=\"utf-8\", errors=\"replace\").read().splitlines() if l.strip()]','    new_lines = [l for l in git_lines if l not in local_set]','    f = open(local_file, \"a\", encoding=\"utf-8\") if new_lines else None','    _ = [f.write(l+chr(10)) for l in new_lines] if f else None','    f.close() if f else None','    print(f\"  Merged {len(new_lines)} new entries.\") if new_lines else print(\"  Already in sync.\")']))"

:restart
echo.
echo Restarting SlugTube container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh your browser (Ctrl+Shift+R)
echo ========================================
echo.
pause
