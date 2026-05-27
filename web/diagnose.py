"""Diagnose all channels: compare disk files vs library DB vs archive."""
import sqlite3
import os
import glob

SHOWS = "/shows"
DB = "/config/slugtube-index.db"
ARCHIVE = "/config/archive/downloaded.txt"

# Load archive video IDs
archive_ids = set()
if os.path.exists(ARCHIVE):
    with open(ARCHIVE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    archive_ids.add(parts[1])
                elif len(parts) == 1:
                    archive_ids.add(parts[0])

# Load DB counts
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
db_counts = {}
for row in conn.execute("SELECT name, video_count FROM channels"):
    db_counts[row['name']] = row['video_count']
conn.close()

# Check each directory in /shows
VIDEO_EXTS = {'.mp4', '.mkv', '.webm', '.m4v'}
problems = []
ok_count = 0

dirs = sorted(os.listdir(SHOWS))
print(f"{'Channel':<40} {'Disk':>6} {'DB':>6} {'Status'}")
print("-" * 70)

for d in dirs:
    path = os.path.join(SHOWS, d)
    if not os.path.isdir(path):
        continue
    
    # Count video files on disk
    disk_videos = 0
    for root, _, files in os.walk(path):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                disk_videos += 1
    
    db_count = db_counts.get(d, 0)
    
    if disk_videos == 0 and db_count == 0:
        status = "⚠️  EMPTY - no files"
        problems.append((d, "empty"))
    elif disk_videos > 0 and db_count == 0:
        status = "🔄 FILES EXIST - needs reindex"
        problems.append((d, "needs_reindex"))
    elif abs(disk_videos - db_count) > 5:
        status = f"📊 MISMATCH (diff: {disk_videos - db_count})"
        problems.append((d, "mismatch"))
    else:
        status = "✅"
        ok_count += 1
    
    print(f"{d:<40} {disk_videos:>6} {db_count:>6} {status}")

print(f"\n{'='*70}")
print(f"Total: {len(dirs)} channels | {ok_count} OK | {len(problems)} need attention")

if problems:
    empty = [p[0] for p in problems if p[1] == "empty"]
    if empty:
        print(f"\n⚠️  Empty channels ({len(empty)}):")
        for c in empty:
            print(f"   - {c}")
        print("   These may have archive entries preventing downloads.")
        print("   Fix: remove their IDs from downloaded.txt")
