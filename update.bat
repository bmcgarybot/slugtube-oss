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

REM Smart merge: repo is source of truth for URLs, local additions are preserved
REM - Channels in repo overwrite local (URL may have changed)
REM - Local-only channels (not in repo) are kept
REM - Backup is always made first

if exist "C:\SlugTube\channels.txt" (
    copy /Y "C:\SlugTube\channels.txt" "C:\SlugTube\channels.txt.bak" >nul
    echo   Backed up existing channels.txt
)

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo   Python not found - replacing channels.txt with repo version.
    copy /Y "C:\SlugTube-new\channels.txt" "C:\SlugTube\channels.txt" >nul
    goto :restart
)

python -c "
import os
repo = r'C:\SlugTube-new\channels.txt'
local = r'C:\SlugTube\channels.txt'

if not os.path.exists(local):
    import shutil; shutil.copy2(repo, local)
    print('  Fresh copy (no local file).')
else:
    # Parse channel entries as name->line mappings
    def parse(path):
        entries = {}  # name -> full_line
        comments = []
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if not line.strip() or line.strip().startswith('#'):
                    comments.append(line)
                    continue
                parts = line.split('|', 1)
                name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                entries[name] = line
        return entries, comments

    repo_entries, repo_comments = parse(repo)
    local_entries, _ = parse(local)

    # Start with repo (source of truth for URLs)
    merged = dict(repo_entries)

    # Add local-only channels (ones you added manually that aren't in repo)
    local_only = 0
    for name, line in local_entries.items():
        if name not in merged:
            merged[name] = line
            local_only += 1

    # Write merged result, preserving repo's comment structure
    with open(local, 'w', encoding='utf-8') as f:
        for c in repo_comments:
            f.write(c + '\n')
        for name, line in merged.items():
            if line not in repo_comments:
                f.write(line + '\n')

    changed = sum(1 for n in repo_entries if n in local_entries and local_entries[n] != repo_entries[n])
    new = sum(1 for n in repo_entries if n not in local_entries)
    print(f'  Merged: {changed} updated, {new} new from repo, {local_only} local-only kept.')
"

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
