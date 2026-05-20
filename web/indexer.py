"""
SlugTube Media Indexer
Scans .info.json files and builds a SQLite index for the library/player.
Runs in background, incremental updates — no 5-hour rescan.
"""

import sqlite3
import os
import json
import time
import threading
import logging
from pathlib import Path
from datetime import datetime

DB_PATH = "/config/slugtube-index.db"
SHOWS_DIR = "/shows"
LOG_FILE = "/config/logs/slugtube.log"

# Set up logger that writes to both stdout and the log file
_logger = logging.getLogger("indexer")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [indexer] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    _logger.addHandler(_sh)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        _fh = logging.FileHandler(LOG_FILE)
        _fh.setFormatter(_fmt)
        _logger.addHandler(_fh)
    except Exception:
        pass

log = _logger.info
log_warn = _logger.warning
log_err = _logger.error

_index_lock = threading.Lock()
_index_status = {"scanning": False, "last_scan": 0, "total": 0, "channels": 0}


def get_db():
    """Get a SQLite connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            path TEXT NOT NULL,
            has_poster INTEGER DEFAULT 0,
            has_banner INTEGER DEFAULT 0,
            has_nfo INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            total_size_bytes INTEGER DEFAULT 0,
            updated_at TEXT
        );
    """)
    # Migrate: add has_banner column if missing
    try:
        conn.execute("SELECT has_banner FROM channels LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE channels ADD COLUMN has_banner INTEGER DEFAULT 0")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            channel_name TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            upload_date TEXT,
            duration INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            file_path TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            thumbnail_path TEXT DEFAULT '',
            subtitle_path TEXT DEFAULT '',
            season TEXT DEFAULT '',
            ext TEXT DEFAULT 'mp4',
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            json_path TEXT NOT NULL,
            json_mtime REAL DEFAULT 0,
            indexed_at TEXT,
            FOREIGN KEY (channel_name) REFERENCES channels(name)
        );

        CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_name);
        CREATE INDEX IF NOT EXISTS idx_videos_upload ON videos(upload_date DESC);
        CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);
        CREATE INDEX IF NOT EXISTS idx_videos_duration ON videos(duration);
        CREATE INDEX IF NOT EXISTS idx_videos_size ON videos(file_size DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
            id UNINDEXED,
            title,
            description,
            channel_name,
            content='videos',
            content_rowid='rowid'
        );

        CREATE TABLE IF NOT EXISTS watch_history (
            video_id TEXT PRIMARY KEY,
            position_seconds REAL DEFAULT 0,
            watched INTEGER DEFAULT 0,
            last_watched TEXT,
            FOREIGN KEY (video_id) REFERENCES videos(id)
        );

        DROP TABLE IF EXISTS video_playlists;
        DROP TABLE IF EXISTS playlists;

        CREATE TABLE playlists (
            id TEXT PRIMARY KEY,
            channel_name TEXT NOT NULL,
            title TEXT NOT NULL,
            video_count INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE video_playlists (
            video_id TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            playlist_index INTEGER DEFAULT 0,
            PRIMARY KEY (video_id, playlist_id)
        );

        CREATE INDEX IF NOT EXISTS idx_video_playlists_playlist ON video_playlists(playlist_id);
    """)
    conn.commit()
    conn.close()


def _find_video_file(info_json_path):
    """Given an .info.json path, find the matching video file."""
    base = str(info_json_path).replace('.info.json', '')
    for ext in ('.mp4', '.mkv', '.webm', '.m4v'):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate, ext.lstrip('.')
    return None, None


def _find_thumbnail(info_json_path):
    """Find thumbnail file matching the video."""
    base = str(info_json_path).replace('.info.json', '')
    # Check direct match first, then -thumb variant (PinchFlat naming)
    for ext in ('.jpg', '.png', '.webp'):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    for ext in ('.jpg', '.png', '.webp'):
        candidate = base + '-thumb' + ext
        if os.path.isfile(candidate):
            return candidate
    return ''


def _find_subtitle(info_json_path):
    """Find subtitle file matching the video."""
    base = str(info_json_path).replace('.info.json', '')
    for pattern in ('.en.srt', '.en.vtt', '.srt', '.vtt'):
        candidate = base + pattern
        if os.path.isfile(candidate):
            return candidate
    # Also check broader patterns
    parent = os.path.dirname(info_json_path)
    video_id = os.path.basename(base)
    for f in os.listdir(parent):
        if video_id in f and (f.endswith('.srt') or f.endswith('.vtt')):
            return os.path.join(parent, f)
    return ''


def _parse_filename(fname):
    """Extract video ID, title, and date from filename patterns.
    SlugTube:   s2026e20260515 - Title Here [VIDEO_ID].mp4
    PinchFlat:  Title Here [VIDEO_ID].mp4  or  Title Here.mp4
    """
    import re

    base = os.path.splitext(fname)[0]

    # Try to extract video ID from [brackets]
    vid_match = re.search(r'\[([A-Za-z0-9_-]{11})\]', base)
    video_id = vid_match.group(1) if vid_match else ''

    # Try to extract date from SlugTube naming: s2026e20260515
    date_match = re.search(r's(\d{4})e(\d{8})', base)
    upload_date = ''
    if date_match:
        raw = date_match.group(2)
        upload_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"

    # Extract title: strip the episode prefix and [ID] suffix
    title = base
    title = re.sub(r'^s\d{4}e\d{8}\s*-\s*', '', title)  # Remove SlugTube prefix
    title = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\]\s*$', '', title)  # Remove [ID] suffix
    title = title.strip(' -')

    if not title:
        title = base

    return video_id, title, upload_date


def _find_thumb_for_video(video_path):
    """Find thumbnail matching a video file path."""
    base = os.path.splitext(video_path)[0]
    # Check direct match first, then -thumb variant (PinchFlat naming)
    for ext in ('.jpg', '.png', '.webp'):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    for ext in ('.jpg', '.png', '.webp'):
        candidate = base + '-thumb' + ext
        if os.path.isfile(candidate):
            return candidate
    return ''


def _find_sub_for_video(video_path):
    """Find subtitle matching a video file path."""
    base = os.path.splitext(video_path)[0]
    for pattern in ('.en.srt', '.en.vtt', '.srt', '.vtt'):
        candidate = base + pattern
        if os.path.isfile(candidate):
            return candidate
    return ''


def _scan_channel(conn, channel_dir):
    """Scan a single channel directory and update the index.
    Strategy: index from .info.json when available, fall back to filename parsing for videos without metadata.
    OPTIMIZED: uses in-memory file set from os.walk() — no extra os.path.isfile() calls.
    """
    channel_name = channel_dir.name
    channel_path = str(channel_dir)

    # Check poster/nfo using the file set we'll build, not stat calls
    has_poster = False
    has_banner = False
    has_nfo = False

    video_count = 0
    total_size = 0
    now = datetime.utcnow().isoformat()

    VIDEO_EXTS = {'.mp4', '.mkv', '.webm', '.m4v'}
    THUMB_EXTS = ('.jpg', '.png', '.webp')
    SUB_PATTERNS = ('.en.srt', '.en.vtt', '.srt', '.vtt')

    # Insert channel record FIRST so video FOREIGN KEYs can reference it
    conn.execute("""
        INSERT OR IGNORE INTO channels (name, path, has_poster, has_banner, has_nfo, video_count, total_size_bytes, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
    """, (channel_name, channel_path, int(has_poster), int(has_banner), int(has_nfo), now))

    # Walk all season dirs and loose files
    for root, dirs, files in os.walk(channel_path):
        # Build a set of filenames for O(1) in-memory lookups (no filesystem calls)
        file_set = set(files)

        # Check for poster/nfo/banner in channel root
        if root == channel_path:
            has_poster = 'poster.jpg' in file_set or 'folder.jpg' in file_set
            has_banner = 'banner.jpg' in file_set or 'fanart.jpg' in file_set
            has_nfo = 'tvshow.nfo' in file_set

        json_files = [f for f in files if f.endswith('.info.json')]
        video_files = {f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTS}

        # Track which video files get indexed via .info.json
        indexed_videos = set()

        # Helper: find matching file using in-memory set
        def _find_in_set(base_name, extensions):
            """Check if base_name + any extension exists in file_set."""
            for ext in extensions:
                candidate = base_name + ext
                if candidate in file_set:
                    return os.path.join(root, candidate)
            return None

        # ── Pass 1: Index from .info.json (rich metadata) ──
        for fname in json_files:
            json_path = os.path.join(root, fname)
            base_name = fname[:-len('.info.json')]  # strip .info.json

            try:
                json_mtime = os.path.getmtime(json_path)
            except OSError:
                continue

            # Check if already indexed and unchanged
            existing = conn.execute(
                "SELECT json_mtime FROM videos WHERE json_path = ?",
                (json_path,)
            ).fetchone()

            if existing and abs(existing['json_mtime'] - json_mtime) < 1:
                row = conn.execute(
                    "SELECT file_path, file_size FROM videos WHERE json_path = ?",
                    (json_path,)
                ).fetchone()
                if row:
                    video_count += 1
                    total_size += row['file_size'] or 0
                    indexed_videos.add(os.path.basename(row['file_path']))
                continue

            # Parse info.json
            try:
                with open(json_path, 'r', errors='replace') as f:
                    info = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            video_id = info.get('id', '')
            if not video_id:
                continue

            # Find actual video file — in-memory lookup
            video_file = _find_in_set(base_name, ('.mp4', '.mkv', '.webm', '.m4v'))
            if not video_file:
                continue
            ext = os.path.splitext(video_file)[1].lstrip('.').lower()

            indexed_videos.add(os.path.basename(video_file))

            file_size = 0
            try:
                file_size = os.path.getsize(video_file)
            except OSError:
                pass

            # Find thumbnail — in-memory lookup
            thumb = (_find_in_set(base_name, THUMB_EXTS)
                     or _find_in_set(base_name + '-thumb', THUMB_EXTS)
                     or '')

            # Find subtitle — in-memory lookup
            subtitle = ''
            for pat in SUB_PATTERNS:
                candidate = base_name + pat
                if candidate in file_set:
                    subtitle = os.path.join(root, candidate)
                    break
            if not subtitle:
                # Broader search: any .srt/.vtt containing the video ID
                for f in files:
                    if video_id in f and (f.endswith('.srt') or f.endswith('.vtt')):
                        subtitle = os.path.join(root, f)
                        break

            parent_name = os.path.basename(root)
            season = parent_name if parent_name.startswith('Season') else ''

            upload_date = info.get('upload_date', '')
            if upload_date and len(upload_date) == 8 and upload_date.isdigit():
                upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

            title = info.get('title', os.path.basename(video_file))
            description = (info.get('description', '') or '')[:2000]

            conn.execute("""
                INSERT OR REPLACE INTO videos
                (id, channel_name, title, description, upload_date, duration,
                 view_count, like_count, file_path, file_size, thumbnail_path,
                 subtitle_path, season, ext, width, height, json_path, json_mtime, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id,
                channel_name,
                title,
                description,
                upload_date,
                info.get('duration', 0) or 0,
                info.get('view_count', 0) or 0,
                info.get('like_count', 0) or 0,
                video_file,
                file_size,
                thumb,
                subtitle,
                season,
                ext or 'mp4',
                info.get('width', 0) or 0,
                info.get('height', 0) or 0,
                json_path,
                json_mtime,
                now,
            ))

            conn.execute("""
                INSERT OR REPLACE INTO videos_fts(rowid, id, title, description, channel_name)
                SELECT rowid, id, title, description, channel_name FROM videos WHERE id = ?
            """, (video_id,))

            # ── Playlist metadata ──
            pl_id = info.get('playlist_id') or ''
            pl_title = info.get('playlist_title') or ''
            pl_index = info.get('playlist_index') or 0
            if pl_id and pl_title:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO playlists (id, channel_name, title, video_count, updated_at)
                        VALUES (?, ?, ?, 0, ?)
                    """, (pl_id, channel_name, pl_title, now))
                    conn.execute("""
                        INSERT OR REPLACE INTO video_playlists (video_id, playlist_id, playlist_index)
                        VALUES (?, ?, ?)
                    """, (video_id, pl_id, pl_index if isinstance(pl_index, int) else 0))
                except Exception:
                    pass

            video_count += 1
            total_size += file_size

        # ── Pass 2: Index video files WITHOUT .info.json ──
        for vfname in video_files:
            if vfname in indexed_videos:
                continue

            video_path = os.path.join(root, vfname)

            # Check if already indexed by file_path
            existing = conn.execute(
                "SELECT id, file_size FROM videos WHERE file_path = ?",
                (video_path,)
            ).fetchone()
            if existing:
                video_count += 1
                total_size += existing['file_size'] or 0
                continue

            video_id, title, upload_date = _parse_filename(vfname)

            if not video_id:
                import hashlib
                video_id = 'f_' + hashlib.md5(video_path.encode()).hexdigest()[:10]

            file_size = 0
            try:
                file_size = os.path.getsize(video_path)
            except OSError:
                pass

            vbase = os.path.splitext(vfname)[0]
            ext = os.path.splitext(vfname)[1].lstrip('.').lower()
            thumb = (_find_in_set(vbase, THUMB_EXTS)
                     or _find_in_set(vbase + '-thumb', THUMB_EXTS)
                     or '')
            subtitle = ''
            for pat in SUB_PATTERNS:
                candidate = vbase + pat
                if candidate in file_set:
                    subtitle = os.path.join(root, candidate)
                    break

            parent_name = os.path.basename(root)
            season = parent_name if parent_name.startswith('Season') else ''

            if not upload_date:
                try:
                    mtime = os.path.getmtime(video_path)
                    upload_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                except OSError:
                    pass

            conn.execute("""
                INSERT OR REPLACE INTO videos
                (id, channel_name, title, description, upload_date, duration,
                 view_count, like_count, file_path, file_size, thumbnail_path,
                 subtitle_path, season, ext, width, height, json_path, json_mtime, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id,
                channel_name,
                title,
                '',
                upload_date,
                0,
                0, 0,
                video_path,
                file_size,
                thumb,
                subtitle,
                season,
                ext or 'mp4',
                0, 0,
                '',
                0,
                now,
            ))

            conn.execute("""
                INSERT OR REPLACE INTO videos_fts(rowid, id, title, description, channel_name)
                SELECT rowid, id, title, description, channel_name FROM videos WHERE id = ?
            """, (video_id,))

            video_count += 1
            total_size += file_size

    # Update channel record
    conn.execute("""
        INSERT OR REPLACE INTO channels (name, path, has_poster, has_banner, has_nfo, video_count, total_size_bytes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (channel_name, channel_path, int(has_poster), int(has_banner), int(has_nfo), video_count, total_size, now))

    # Update playlist video counts
    try:
        conn.execute("""
            UPDATE playlists SET video_count = (
                SELECT COUNT(*) FROM video_playlists WHERE playlist_id = playlists.id
            ), updated_at = ?
            WHERE channel_name = ?
        """, (now, channel_name))
    except Exception:
        pass

    return video_count, total_size


def run_index(force=False):
    """Full index scan. Incremental — only re-indexes changed .info.json files."""
    if _index_status["scanning"]:
        return

    with _index_lock:
        _index_status["scanning"] = True
        start = time.time()
        log("📚 Starting scan...")

        try:
            init_db()
            conn = get_db()

            shows = Path(SHOWS_DIR)
            if not shows.exists():
                log_err(f"❌ SHOWS_DIR '{SHOWS_DIR}' does not exist! Check volume mount.")
                _index_status["scanning"] = False
                return

            # Log what we can see
            top_dirs = [d.name for d in shows.iterdir() if d.is_dir()]
            log(f"📚 Found {len(top_dirs)} channels in {SHOWS_DIR}")
            if not top_dirs:
                log_err(f"❌ {SHOWS_DIR} is empty! Volume mount may have failed.")
                _index_status["scanning"] = False
                return

            total_videos = 0
            total_channels = 0

            channel_dirs = sorted([d for d in shows.iterdir() if d.is_dir()])
            num_dirs = len(channel_dirs)

            for i, channel_dir in enumerate(channel_dirs, 1):
                try:
                    ch_start = time.time()
                    # Quick skip: if channel dir mtime hasn't changed since last index, skip
                    if not force:
                        existing_ch = conn.execute(
                            "SELECT updated_at, video_count FROM channels WHERE name = ?",
                            (channel_dir.name,)
                        ).fetchone()
                        if existing_ch and existing_ch['video_count'] > 0:
                            try:
                                dir_mtime = os.path.getmtime(str(channel_dir))
                                last_update = existing_ch['updated_at']
                                if last_update:
                                    last_dt = datetime.fromisoformat(last_update)
                                    dir_dt = datetime.utcfromtimestamp(dir_mtime)
                                    # If dir hasn't been modified since last scan, skip
                                    if dir_dt < last_dt:
                                        total_videos += existing_ch['video_count']
                                        total_channels += 1
                                        continue
                            except (ValueError, OSError):
                                pass

                    log(f"  [{i}/{num_dirs}] Scanning: {channel_dir.name}")
                    vcount, _ = _scan_channel(conn, channel_dir)
                    ch_elapsed = time.time() - ch_start
                    total_videos += vcount
                    total_channels += 1
                    log(f"  [{i}/{num_dirs}] ✅ {channel_dir.name}: {vcount} videos ({ch_elapsed:.1f}s)")
                    # Commit per channel so data is visible immediately
                    conn.commit()
                except BaseException as e:
                    log_warn(f"  [{i}/{num_dirs}] ⚠️ Error indexing {channel_dir.name}: {type(e).__name__}: {e}")
                    import traceback
                    log_warn(traceback.format_exc())
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    continue

            # Clean up videos whose files no longer exist
            if force:
                cursor = conn.execute("SELECT id, file_path FROM videos")
                dead = []
                for row in cursor:
                    if not os.path.isfile(row['file_path']):
                        dead.append(row['id'])
                if dead:
                    conn.executemany("DELETE FROM videos WHERE id = ?", [(d,) for d in dead])
                    log(f"  🗑️ Removed {len(dead)} orphaned entries")

            conn.commit()
            conn.close()

            elapsed = time.time() - start
            _index_status["total"] = total_videos
            _index_status["channels"] = total_channels
            _index_status["last_scan"] = time.time()
            log(f"📚 Indexer done — {total_videos} videos across {total_channels} channels ({elapsed:.1f}s)")

        except BaseException as e:
            import traceback
            log_err(f"❌ Indexer error: {type(e).__name__}: {e}")
            log_err(traceback.format_exc())
        finally:
            _index_status["scanning"] = False


def start_background_index(force=False):
    """Run indexer in a background thread."""
    thread = threading.Thread(target=run_index, args=(force,), daemon=True)
    thread.start()
    return thread


def get_index_status():
    return dict(_index_status)


# ── Query helpers ──

def get_all_channels():
    """Get all channels with stats."""
    conn = get_db()
    rows = conn.execute("""
        SELECT name, path, has_poster, has_nfo, video_count, total_size_bytes, updated_at
        FROM channels ORDER BY name COLLATE NOCASE
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channel_videos(channel_name, sort='newest', limit=200, offset=0):
    """Get videos for a specific channel."""
    order_map = {
        'newest': 'upload_date DESC, title ASC',
        'oldest': 'upload_date ASC, title ASC',
        'title': 'title COLLATE NOCASE ASC',
        'largest': 'file_size DESC',
        'smallest': 'file_size ASC',
        'longest': 'duration DESC',
        'shortest': 'duration ASC',
        'most_viewed': 'view_count DESC',
    }
    order = order_map.get(sort, 'upload_date DESC, title ASC')

    conn = get_db()
    rows = conn.execute(f"""
        SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
        FROM videos v
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        WHERE v.channel_name = ?
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, (channel_name, limit, offset)).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM videos WHERE channel_name = ?",
        (channel_name,)
    ).fetchone()['cnt']

    conn.close()
    return [dict(r) for r in rows], total


def get_video(video_id):
    """Get a single video by ID."""
    conn = get_db()
    row = conn.execute("""
        SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
        FROM videos v
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        WHERE v.id = ?
    """, (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def search_videos(query, limit=50):
    """Full-text search across titles and descriptions."""
    conn = get_db()
    # Clean query for FTS5
    safe_q = query.replace('"', '').strip()
    if not safe_q:
        return []

    try:
        rows = conn.execute("""
            SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
            FROM videos_fts fts
            JOIN videos v ON v.rowid = fts.rowid
            LEFT JOIN watch_history wh ON v.id = wh.video_id
            WHERE videos_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (f'"{safe_q}" OR {safe_q}*', limit)).fetchall()
    except Exception:
        # Fallback to LIKE search if FTS fails
        rows = conn.execute("""
            SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
            FROM videos v
            LEFT JOIN watch_history wh ON v.id = wh.video_id
            WHERE v.title LIKE ? OR v.description LIKE ?
            ORDER BY v.upload_date DESC
            LIMIT ?
        """, (f'%{safe_q}%', f'%{safe_q}%', limit)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_recent_videos(limit=50):
    """Get most recently added videos across all channels."""
    conn = get_db()
    rows = conn.execute("""
        SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
        FROM videos v
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        ORDER BY v.indexed_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_watch_progress(video_id, position_seconds, watched=False):
    """Update watch position/status for a video."""
    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO watch_history (video_id, position_seconds, watched, last_watched)
        VALUES (?, ?, ?, ?)
    """, (video_id, position_seconds, int(watched), now))
    conn.commit()
    conn.close()


def get_library_stats():
    """Quick stats for dashboard."""
    conn = get_db()
    stats = {}
    row = conn.execute("SELECT COUNT(*) as cnt, SUM(file_size) as total_size FROM videos").fetchone()
    stats['total_videos'] = row['cnt'] or 0
    stats['total_size_bytes'] = row['total_size'] or 0
    stats['total_size_gb'] = round((row['total_size'] or 0) / (1024**3), 1)

    row = conn.execute("SELECT COUNT(*) as cnt FROM channels").fetchone()
    stats['total_channels'] = row['cnt'] or 0

    row = conn.execute("SELECT COUNT(*) as cnt FROM watch_history WHERE watched = 1").fetchone()
    stats['watched_count'] = row['cnt'] or 0

    conn.close()
    return stats


def get_channel_playlists(channel_name):
    """Get all playlists for a channel, ordered by title."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, channel_name, title, video_count, updated_at
        FROM playlists
        WHERE channel_name = ?
        ORDER BY title COLLATE NOCASE
    """, (channel_name,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_playlist_videos(playlist_id, sort='playlist_index', limit=200, offset=0):
    """Get videos in a playlist with sort and pagination."""
    order_map = {
        'playlist_index': 'vp.playlist_index ASC, v.title ASC',
        'newest': 'v.upload_date DESC, v.title ASC',
        'oldest': 'v.upload_date ASC, v.title ASC',
        'title': 'v.title COLLATE NOCASE ASC',
        'largest': 'v.file_size DESC',
        'longest': 'v.duration DESC',
        'most_viewed': 'v.view_count DESC',
    }
    order = order_map.get(sort, 'vp.playlist_index ASC, v.title ASC')

    conn = get_db()
    rows = conn.execute(f"""
        SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched, vp.playlist_index
        FROM video_playlists vp
        JOIN videos v ON v.id = vp.video_id
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        WHERE vp.playlist_id = ?
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, (playlist_id, limit, offset)).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM video_playlists WHERE playlist_id = ?",
        (playlist_id,)
    ).fetchone()['cnt']

    conn.close()
    return [dict(r) for r in rows], total


def get_playlist(playlist_id):
    """Get a single playlist by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
