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
    conn = sqlite3.connect(DB_PATH, timeout=30)
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
        CREATE INDEX IF NOT EXISTS idx_videos_jsonpath ON videos(json_path);

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

        CREATE TABLE IF NOT EXISTS playlists (
            id TEXT PRIMARY KEY,
            channel_name TEXT NOT NULL,
            title TEXT NOT NULL,
            video_count INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS video_playlists (
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

        log(f"    📁 {os.path.basename(root)}: {len(json_files)} json, {len(video_files)} videos, {len(files)} total files")

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
        json_processed = 0
        for fname in json_files:
            json_path = os.path.join(root, fname)
            base_name = fname[:-len('.info.json')]  # strip .info.json

            try:
                json_mtime = os.path.getmtime(json_path)
            except OSError:
                continue

            # Check if already indexed and unchanged
            existing = conn.execute(
                "SELECT json_mtime, file_path, file_size FROM videos WHERE json_path = ?",
                (json_path,)
            ).fetchone()

            if existing and abs(existing['json_mtime'] - json_mtime) < 1:
                if existing['file_path']:
                    video_count += 1
                    total_size += existing['file_size'] or 0
                    indexed_videos.add(os.path.basename(existing['file_path']))
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
                except Exception as pe:
                    log_warn(f"    ⚠️ Playlist insert error: {pe}")

            video_count += 1
            total_size += file_size
            json_processed += 1
            if json_processed % 100 == 0:
                log(f"    ... processed {json_processed}/{len(json_files)} info.json files")

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


def index_single_video(file_path):
    """Index a single video file immediately after download.
    Called by the --exec after_move hook via /api/index-video.
    Finds the .info.json, parses metadata, inserts into DB, and updates channel counts.
    Returns (video_id, channel_name) on success or (None, error_msg) on failure.
    """
    from pathlib import Path as _P

    fp = _P(file_path)
    if not fp.exists():
        return None, f"File not found: {file_path}"

    VIDEO_EXTS = {'.mp4', '.mkv', '.webm', '.m4v'}
    if fp.suffix.lower() not in VIDEO_EXTS:
        return None, f"Not a video file: {file_path}"

    # Derive channel name and season from path: /shows/ChannelName/Season YYYY/video.mp4
    shows = _P(SHOWS_DIR)
    try:
        rel = fp.relative_to(shows)
        parts = rel.parts  # ('ChannelName', 'Season 2026', 'video.mp4') or ('ChannelName', 'video.mp4')
    except ValueError:
        return None, f"File not under {SHOWS_DIR}: {file_path}"

    if len(parts) < 2:
        return None, f"Unexpected path structure: {file_path}"

    channel_name = parts[0]
    # Strip trailing '#' from channel folder name to match channels.txt naming
    display_name = channel_name.rstrip('#') if channel_name.endswith('#') else channel_name
    channel_dir = shows / channel_name
    season = parts[1] if len(parts) >= 3 and parts[1].startswith('Season') else ''

    # Find .info.json
    base_name = fp.stem  # filename without extension
    json_path = str(fp.with_suffix('.info.json'))
    # yt-dlp sometimes names it differently — also check stripping the extension first
    if not os.path.exists(json_path):
        # Try: video.mp4 -> video.info.json  (already tried above)
        # Try: video.f137.mp4 -> video.info.json (strip format code)
        alt_base = base_name.rsplit('.', 1)[0] if '.' in base_name else base_name
        alt_json = os.path.join(str(fp.parent), alt_base + '.info.json')
        if os.path.exists(alt_json):
            json_path = alt_json
        else:
            json_path = None

    now = datetime.utcnow().isoformat()
    file_size = fp.stat().st_size

    conn = get_db()
    try:
        # Ensure channel exists in DB
        conn.execute("""
            INSERT OR IGNORE INTO channels (name, path, has_poster, has_banner, has_nfo, video_count, total_size_bytes, updated_at)
            VALUES (?, ?, 0, 0, 0, 0, 0, ?)
        """, (display_name, str(channel_dir), now))

        if json_path and os.path.exists(json_path):
            # Rich metadata from .info.json
            try:
                with open(json_path, 'r', errors='replace') as f:
                    info = json.load(f)
            except (json.JSONDecodeError, OSError):
                info = {}

            video_id = info.get('id', '')
            if not video_id:
                # Extract from filename: ... [VIDEO_ID].mp4
                import re
                m = re.search(r'\[([A-Za-z0-9_-]{11})\]', base_name)
                video_id = m.group(1) if m else 'f_' + base_name[:16]

            upload_date = info.get('upload_date', '')
            if upload_date and len(upload_date) == 8 and upload_date.isdigit():
                upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

            title = info.get('title', base_name)
            description = (info.get('description', '') or '')[:2000]

            # Find thumbnail
            THUMB_EXTS = ('.jpg', '.png', '.webp')
            thumb = ''
            jbase = os.path.splitext(os.path.basename(json_path))[0].replace('.info', '')
            for ext in THUMB_EXTS:
                candidate = os.path.join(str(fp.parent), jbase + ext)
                if os.path.exists(candidate):
                    thumb = candidate
                    break

            # Find subtitle
            subtitle = ''
            for pat in ('.en.srt', '.en.vtt', '.srt', '.vtt'):
                candidate = os.path.join(str(fp.parent), jbase + pat)
                if os.path.exists(candidate):
                    subtitle = candidate
                    break

            json_mtime = os.path.getmtime(json_path)

            conn.execute("""
                INSERT OR REPLACE INTO videos
                (id, channel_name, title, description, upload_date, duration,
                 view_count, like_count, file_path, file_size, thumbnail_path,
                 subtitle_path, season, ext, width, height, json_path, json_mtime, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id, display_name, title, description, upload_date,
                info.get('duration', 0) or 0,
                info.get('view_count', 0) or 0,
                info.get('like_count', 0) or 0,
                str(fp), file_size, thumb, subtitle, season,
                fp.suffix.lstrip('.').lower() or 'mp4',
                info.get('width', 0) or 0,
                info.get('height', 0) or 0,
                json_path, json_mtime, now,
            ))

            conn.execute("""
                INSERT OR REPLACE INTO videos_fts(rowid, id, title, description, channel_name)
                SELECT rowid, id, title, description, channel_name FROM videos WHERE id = ?
            """, (video_id,))

        else:
            # No .info.json — parse from filename
            video_id, title, upload_date = _parse_filename(fp.name)
            if not video_id:
                import hashlib
                video_id = 'f_' + hashlib.md5(file_path.encode()).hexdigest()[:10]
            if not upload_date:
                try:
                    mtime = os.path.getmtime(str(fp))
                    upload_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                except OSError:
                    upload_date = ''

            conn.execute("""
                INSERT OR REPLACE INTO videos
                (id, channel_name, title, description, upload_date, duration,
                 view_count, like_count, file_path, file_size, thumbnail_path,
                 subtitle_path, season, ext, width, height, json_path, json_mtime, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id, display_name, title or base_name, '', upload_date,
                0, 0, 0, str(fp), file_size, '', '', season,
                fp.suffix.lstrip('.').lower() or 'mp4',
                0, 0, '', 0, now,
            ))

            conn.execute("""
                INSERT OR REPLACE INTO videos_fts(rowid, id, title, description, channel_name)
                SELECT rowid, id, title, description, channel_name FROM videos WHERE id = ?
            """, (video_id,))

        # Update channel video count and size
        row = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(file_size),0) as sz FROM videos WHERE channel_name = ?",
            (display_name,)
        ).fetchone()
        conn.execute(
            "UPDATE channels SET video_count = ?, total_size_bytes = ?, updated_at = ? WHERE name = ?",
            (row['cnt'], row['sz'], now, display_name)
        )

        conn.commit()
        log(f"  ✅ Indexed video: {title if json_path else base_name} ({video_id}) → {display_name}")
        return video_id, display_name

    except Exception as e:
        log_warn(f"  ❌ index_single_video error: {e}")
        return None, str(e)
    finally:
        conn.close()


def _index_single_playlist(playlist_url, channel_name):
    """Index a single playlist URL (not a channel's /playlists tab).
    Used when channels.txt has a direct playlist URL like youtube.com/playlist?list=..."""
    import subprocess as _sp
    import json as _json
    import re as _re
    from urllib.parse import parse_qs, urlparse

    # Extract playlist ID from URL
    parsed = urlparse(playlist_url)
    qs = parse_qs(parsed.query)
    pl_id = qs.get('list', [''])[0]
    if not pl_id:
        log_warn(f"  ⚠️ Could not extract playlist ID from {playlist_url}")
        return

    pl_title = channel_name  # Use channel name as playlist title

    log(f"  📋 Indexing playlist: {pl_title} ({pl_id})")

    # Get videos in this playlist
    try:
        result = _sp.run(
            ['yt-dlp', '--no-check-certificates', '--flat-playlist', '--dump-json', playlist_url],
            capture_output=True, text=True, timeout=300
        )
    except Exception as e:
        log_warn(f"  ⚠️ Timeout fetching playlist {pl_title}: {e}")
        return

    video_ids = []
    for line in (result.stdout or '').strip().split('\n'):
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
            vid = entry.get('id', '')
            if vid and _re.match(r'^[A-Za-z0-9_-]{11}$', vid):
                video_ids.append(vid)
        except _json.JSONDecodeError:
            continue

    log(f"    Found {len(video_ids)} videos in playlist")

    # Store in DB
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO playlists (id, channel_name, title, video_count, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
    """, (pl_id, channel_name, pl_title, len(video_ids)))

    # Link videos
    conn.execute("DELETE FROM video_playlists WHERE playlist_id = ?", (pl_id,))
    for idx, vid in enumerate(video_ids):
        db_vid = conn.execute("SELECT id FROM videos WHERE youtube_id = ?", (vid,)).fetchone()
        if db_vid:
            conn.execute("""
                INSERT OR REPLACE INTO video_playlists (video_id, playlist_id, playlist_index)
                VALUES (?, ?, ?)
            """, (db_vid['id'], pl_id, idx))
    conn.commit()
    log(f"    Stored playlist {pl_title} ({len(video_ids)} videos)")


def index_channel_playlists(channel_url, channel_name):
    """Fetch real playlists from YouTube for a channel and populate the DB.

    Uses yt-dlp to:
    1. List all curated playlists on the channel's /playlists tab
    2. For each playlist, get the video IDs
    3. Match against already-downloaded videos in the DB
    4. Replace existing playlist data for this channel
    """
    import subprocess as _sp

    # Derive the /playlists URL from the channel URL
    # e.g. https://www.youtube.com/@MeatCanyon/videos -> https://www.youtube.com/@MeatCanyon/playlists
    # But if it's already a playlist URL (youtube.com/playlist?list=...), skip — it IS a playlist
    if '/playlist?list=' in channel_url:
        log(f"🎵 {channel_name} is a playlist URL — indexing directly as single playlist")
        # This IS a playlist, not a channel. Index it directly instead of looking for /playlists tab
        _index_single_playlist(channel_url, channel_name)
        return

    base_url = channel_url.split('/videos')[0].split('/playlists')[0].rstrip('/')
    playlists_url = base_url + '/playlists'

    log(f"🎵 Fetching playlists for {channel_name} from {playlists_url}")

    # Step 1: Enumerate playlists
    try:
        result = _sp.run(
            ['yt-dlp', '--no-check-certificates', '--flat-playlist', '--dump-json', playlists_url],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log_warn(f"  ⚠️ yt-dlp playlists listing failed for {channel_name}: {result.stderr[:500]}")
            return
    except _sp.TimeoutExpired:
        log_warn(f"  ⚠️ yt-dlp playlists listing timed out for {channel_name}")
        return
    except Exception as e:
        log_err(f"  ❌ yt-dlp playlists listing error for {channel_name}: {e}")
        return

    # Parse playlist entries
    raw_playlists = []
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            pl_id = entry.get('id', '')
            pl_title = entry.get('title', '')
            pl_url = entry.get('url') or entry.get('webpage_url', '')

            # Filter out auto-generated playlists:
            # - UU prefix = uploads playlist
            # - FL prefix = favorites/liked
            # - LL prefix = liked videos
            # - "- Videos" suffix = auto-generated uploads title
            if not pl_id or not pl_title:
                continue
            if pl_id.startswith('UU') or pl_id.startswith('FL') or pl_id.startswith('LL'):
                log(f"  ⏭️ Skipping auto-generated playlist: {pl_title} ({pl_id})")
                continue
            if pl_title.endswith(' - Videos') or pl_title.lower() in ('videos', 'shorts', 'live'):
                log(f"  ⏭️ Skipping auto-generated playlist: {pl_title} ({pl_id})")
                continue

            raw_playlists.append({'id': pl_id, 'title': pl_title, 'url': pl_url})
        except json.JSONDecodeError:
            continue

    log(f"  📋 Found {len(raw_playlists)} curated playlists for {channel_name}")

    if not raw_playlists:
        return

    # Step 2: For each playlist, get video IDs
    now = datetime.utcnow().isoformat()
    playlist_data = []  # list of (pl_id, pl_title, [(video_id, playlist_index), ...])

    for i, pl in enumerate(raw_playlists):
        pl_id = pl['id']
        pl_title = pl['title']
        pl_url = pl['url'] or f"https://www.youtube.com/playlist?list={pl_id}"

        log(f"  [{i+1}/{len(raw_playlists)}] Fetching videos for: {pl_title}")

        try:
            vresult = _sp.run(
                ['yt-dlp', '--no-check-certificates', '--flat-playlist', '--dump-json', pl_url],
                capture_output=True, text=True, timeout=120
            )
        except _sp.TimeoutExpired:
            log_warn(f"    ⚠️ Timed out fetching playlist: {pl_title}")
            continue
        except Exception as e:
            log_warn(f"    ⚠️ Error fetching playlist {pl_title}: {e}")
            continue

        if vresult.returncode != 0:
            log_warn(f"    ⚠️ yt-dlp failed for playlist {pl_title}: {vresult.stderr[:300]}")
            continue

        video_entries = []
        for vline in vresult.stdout.strip().split('\n'):
            if not vline.strip():
                continue
            try:
                ventry = json.loads(vline)
                vid = ventry.get('id', '')
                vidx = ventry.get('playlist_index') or ventry.get('playlist_autonumber', 0)
                if vid:
                    video_entries.append((vid, vidx if isinstance(vidx, int) else 0))
            except json.JSONDecodeError:
                continue

        log(f"    Found {len(video_entries)} videos in playlist")
        playlist_data.append((pl_id, pl_title, video_entries))

        # Sleep between requests to avoid rate limiting
        if i < len(raw_playlists) - 1:
            time.sleep(2)

    # Step 3: Write to DB
    try:
        conn = get_db()

        # Clear existing playlist data for this channel
        existing_pls = conn.execute(
            "SELECT id FROM playlists WHERE channel_name = ?", (channel_name,)
        ).fetchall()
        for row in existing_pls:
            conn.execute("DELETE FROM video_playlists WHERE playlist_id = ?", (row['id'],))
        conn.execute("DELETE FROM playlists WHERE channel_name = ?", (channel_name,))

        total_matched = 0

        for pl_id, pl_title, video_entries in playlist_data:
            # Insert playlist record
            conn.execute("""
                INSERT OR REPLACE INTO playlists (id, channel_name, title, video_count, updated_at)
                VALUES (?, ?, ?, 0, ?)
            """, (pl_id, channel_name, pl_title, now))

            matched = 0
            for vid, vidx in video_entries:
                # Check if this video exists in our library
                exists = conn.execute("SELECT id FROM videos WHERE id = ?", (vid,)).fetchone()
                if exists:
                    conn.execute("""
                        INSERT OR REPLACE INTO video_playlists (video_id, playlist_id, playlist_index)
                        VALUES (?, ?, ?)
                    """, (vid, pl_id, vidx))
                    matched += 1

            # Update playlist video count (matched only)
            conn.execute("""
                UPDATE playlists SET video_count = ? WHERE id = ?
            """, (matched, pl_id))

            total_matched += matched
            log(f"    ✅ {pl_title}: {matched}/{len(video_entries)} videos matched in library")

        conn.commit()
        conn.close()
        log(f"  🎵 Playlist indexing complete for {channel_name}: {len(playlist_data)} playlists, {total_matched} total matches")

    except Exception as e:
        import traceback
        log_err(f"  ❌ DB error during playlist indexing for {channel_name}: {e}")
        log_err(traceback.format_exc())


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


def start_background_index(force=False, playlist_channels=None):
    """Run indexer in a background thread.

    Args:
        force: Force full re-scan even if nothing changed
        playlist_channels: List of dicts with 'url' and 'name' keys for channels
                          that should also have their playlists indexed after the
                          main video index completes.
    """
    def _run():
        run_index(force=force)
        # After video indexing, run playlist indexing for flagged channels
        if playlist_channels:
            log(f"🎵 Starting playlist indexing for {len(playlist_channels)} channels...")
            for ch in playlist_channels:
                try:
                    index_channel_playlists(ch['url'], ch['name'])
                except Exception as e:
                    import traceback
                    log_err(f"❌ Playlist indexing failed for {ch['name']}: {e}")
                    log_err(traceback.format_exc())
            log("🎵 Playlist indexing complete for all flagged channels")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def reindex_single_channel(channel_name):
    """Re-index a single channel directory."""
    shows = Path(SHOWS_DIR)
    
    # Collect all matching dirs: exact name + '#' suffix variant
    candidates = []
    channel_dir = shows / channel_name
    if channel_dir.is_dir():
        candidates.append(channel_dir)
    alt_dir = shows / (channel_name + '#')
    if alt_dir.is_dir():
        candidates.append(alt_dir)
    # Also check if the name already has '#' — don't double-append
    if channel_name.endswith('#'):
        bare_dir = shows / channel_name.rstrip('#')
        if bare_dir.is_dir() and bare_dir not in candidates:
            candidates.append(bare_dir)
    
    if not candidates:
        log_warn(f"⚠️ Channel directory not found: {channel_name}")
        return False

    try:
        conn = get_db()
        init_db()
        total_vcount = 0
        for cdir in candidates:
            log(f"📚 Re-indexing single channel: {cdir.name}")
            ch_start = time.time()
            vcount, _ = _scan_channel(conn, cdir)
            total_vcount += vcount
            elapsed = time.time() - ch_start
            log(f"📚 ✅ {cdir.name}: {vcount} videos ({elapsed:.1f}s)")
        conn.commit()
        log(f"📚 Total for '{channel_name}': {total_vcount} videos across {len(candidates)} dir(s)")
        conn.close()
        return True
    except BaseException as e:
        import traceback
        log_err(f"❌ Error re-indexing {channel_name}: {type(e).__name__}: {e}")
        log_err(traceback.format_exc())
        return False


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
    """Get all playlists for a channel, ordered by title.
    Filters out generic 'uploads' playlists (title ending with ' - Videos').
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT id, channel_name, title, video_count, updated_at
        FROM playlists
        WHERE channel_name = ? AND title NOT LIKE '% - Videos'
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
