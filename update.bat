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
REM Use Python for safe merge — batch can't handle pipe characters in URLs
python -c "
import os
git_file = r'C:\SlugTube-new\channels.txt'
local_file = r'C:\SlugTube\channels.txt'
backup_file = r'C:\SlugTube\channels.txt.bak'

if not os.path.exists(local_file):
    # No local file — fresh copy
    import shutil
    shutil.copy2(git_file, local_file)
    print('  Fresh copy (no local file existed).')
else:
    # Backup local
    import shutil
    shutil.copy2(local_file, backup_file)
    # Read both files
    with open(local_file, 'r', encoding='utf-8', errors='replace') as f:
        local_lines = set(line.rstrip('\n\r') for line in f if line.strip())
    with open(git_file, 'r', encoding='utf-8', errors='replace') as f:
        git_lines = [line.rstrip('\n\r') for line in f if line.strip()]
    # Append new lines from git that aren't in local
    new_lines = [l for l in git_lines if l not in local_lines]
    if new_lines:
        with open(local_file, 'a', encoding='utf-8') as f:
            for line in new_lines:
                f.write(line + '\n')
        print(f'  Merged {len(new_lines)} new entries. Your UI-added channels are safe.')
    else:
        print('  Already in sync. Nothing to merge.')
"
echo.
echo Restarting SlugTube container...
docker restart slugtube
echo.
echo ========================================
echo   Done! Refresh your browser.
echo ========================================
pause
