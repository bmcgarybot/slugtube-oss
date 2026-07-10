from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, abort, Response
import subprocess
import os
import threading
import json
import mimetypes
import re
import time
import zipfile
import sqlite3
import tempfile
import fnmatch
from datetime import datetime
from pathlib import Path

app = Flask(__name__)


@app.route("/api/version")
def api_version():
    """Quick check: what code version is the container running?"""
    return jsonify({"version": "2026-05-29b", "status": "ok"})


@app.route("/api/index-video", methods=["POST"])
def api_index_video():
    """Index a single video file immediately after download.
    Called by yt-dlp --exec after_move hook from download.sh."""
    file_path = request.form.get("file", "").strip() or request.args.get("file", "").strip()
    if not file_path:
        return jsonify({"error": "missing 'file' parameter"}), 400
    from indexer import index_single_video
    video_id, result = index_single_video(file_path)
    if video_id:
        return jsonify({"status": "ok", "video_id": video_id, "channel": result})
    else:
        return jsonify({"status": "error", "error": result}), 500


CHANNELS_FILE = "/config/channels.txt"
ARCHIVE_FILE = "/config/archive/downloaded.txt"
LOG_FILE = "/config/logs/slugtube.log"
SHOWS_DIR = "/shows"
STATE_FILE = "/config/slugtube-state.json"
CONFIG_FILE = "/config/slugtube-config.json"
PAUSE_FILE = "/config/paused"

# Track running jobs
jobs = {"status": "idle", "mode": None, "started": None, "log": "", "proc": None}

def check_stale_job():
    """Reset jobs stuck in 'running' for more than 5 hours."""
    if jobs["status"] == "running" and jobs.get("started"):
        try:
            from datetime import datetime
            started = datetime.strptime(jobs["started"], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - started).total_seconds() > 18000:
                jobs["status"] = "error"
                jobs["log"] = "Job timed out (stale). Reset automatically."
        except Exception:
            pass

# Cache for channel stats (scanning 16TB is slow — runs in background)
_stats_cache = {"data": [], "updated": 0, "scanning": False}
STATS_CACHE_TTL = 300  # 5 minutes

DEFAULT_CONFIG = {
    "quality": "1080p",
    "format": "mp4",
    "embed_subs": True,
    "embed_metadata": True,
    "embed_thumbnail": True,
    "write_nfo": True,
    "write_thumbnail": True,
    "write_subs_file": True,
    "skip_shorts": True,
    "shorts_max_duration": 60,
    "sleep_interval": 3,
    # Schedule config (overrides env vars when set)
    "fast_cron": None,    # None = use env var default
    "full_cron": None,
    "update_cron": None,
    "fast_enabled": True,
    "full_enabled": True,
    "update_enabled": True,
}

# ── Human-friendly schedule presets ──
SCHEDULE_PRESETS = {
    "every_2h":    {"cron": "0 */2 * * *",   "label": "Every 2 hours"},
    "every_4h":    {"cron": "0 */4 * * *",   "label": "Every 4 hours"},
    "every_6h":    {"cron": "0 */6 * * *",   "label": "Every 6 hours"},
    "every_8h":    {"cron": "0 */8 * * *",   "label": "Every 8 hours"},
    "every_12h":   {"cron": "0 */12 * * *",  "label": "Every 12 hours"},
    "daily_midnight": {"cron": "0 0 * * *",     "label": "Daily at 12:00 AM"},
    "daily_1am":   {"cron": "0 1 * * *",     "label": "Daily at 1:00 AM"},
    "daily_3am":   {"cron": "0 3 * * *",     "label": "Daily at 3:00 AM"},
    "daily_6am":   {"cron": "0 6 * * *",     "label": "Daily at 6:00 AM"},
    "daily_noon":  {"cron": "0 12 * * *",    "label": "Daily at 12:00 PM"},
    "weekly_sun":  {"cron": "0 3 * * 0",     "label": "Weekly — Sunday 3:00 AM"},
    "weekly_mon":  {"cron": "0 3 * * 1",     "label": "Weekly — Monday 3:00 AM"},
    "weekly_sat":  {"cron": "0 3 * * 6",     "label": "Weekly — Saturday 3:00 AM"},
    "biweekly":    {"cron": "0 3 1,15 * *",  "label": "Twice a month (1st & 15th)"},
    "monthly":     {"cron": "0 3 1 * *",     "label": "Monthly — 1st at 3:00 AM"},
}

# ── Helpers ──

def format_duration(seconds):
    """Format seconds as H:MM:SS or M:SS."""
    if not seconds:
        return "0:00"
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_size(bytes_val):
    """Format bytes as human readable size."""
    if not bytes_val:
        return "—"
    bytes_val = int(bytes_val)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}" if unit != 'B' else f"{bytes_val} B"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


# Register as Jinja globals so templates can use them
app.jinja_env.globals['format_duration'] = format_duration
app.jinja_env.globals['format_size'] = format_size

@app.before_request
def _check_stale():
    check_stale_job()


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            merged = {**DEFAULT_CONFIG, **cfg}
            return merged
    except:
        return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_timezone():
    """Get current timezone from config > env var > UTC."""
    config = load_config()
    tz = config.get("timezone")
    if tz:
        return tz
    return os.environ.get("TZ", "UTC")


def apply_timezone(tz_name):
    """Apply timezone to the running system: /etc/localtime, env var, Python."""
    import time as _time
    zoneinfo_path = f"/usr/share/zoneinfo/{tz_name}"
    if os.path.isfile(zoneinfo_path):
        try:
            os.remove("/etc/localtime")
        except OSError:
            pass
        os.symlink(zoneinfo_path, "/etc/localtime")
        with open("/etc/timezone", "w") as f:
            f.write(tz_name + "\n")
        os.environ["TZ"] = tz_name
        _time.tzset()
        return True
    return False


def get_timezone_list():
    """Get sorted list of valid timezone names from the system."""
    tz_list = []
    base = "/usr/share/zoneinfo"
    valid_prefixes = ('Africa', 'America', 'Antarctica', 'Arctic', 'Asia',
                      'Atlantic', 'Australia', 'Europe', 'Indian', 'Pacific', 'US')
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            if rel == '.':
                continue
            top = rel.split('/')[0]
            if top not in valid_prefixes:
                continue
            for fname in files:
                if fname.startswith('.') or '+' in fname:
                    continue
                tz_path = os.path.join(rel, fname)
                tz_list.append(tz_path)
    # Fallback: use Python's zoneinfo module if filesystem walk found nothing
    if not tz_list:
        try:
            from zoneinfo import available_timezones
            for tz in available_timezones():
                parts = tz.split('/')
                if len(parts) >= 2 and parts[0] in valid_prefixes:
                    tz_list.append(tz)
        except ImportError:
            # Absolute last resort — hardcoded common US timezones
            tz_list = [
                "America/New_York", "America/Chicago", "America/Denver",
                "America/Los_Angeles", "America/Phoenix", "America/Anchorage",
                "Pacific/Honolulu", "US/Eastern", "US/Central", "US/Mountain",
                "US/Pacific", "US/Arizona", "US/Hawaii", "UTC",
            ]
    tz_list.sort()
    return tz_list


def get_schedule(config, key, env_default):
    """Get cron expression: config override > env var > hardcoded default."""
    val = config.get(key)
    if val:
        return val
    return os.environ.get(key.upper().replace("_cron", "_CRON").replace("fast_cron", "FAST_CRON").replace("full_cron", "FULL_CRON").replace("update_cron", "UPDATE_CRON"), env_default)


def rebuild_cron(config=None):
    """Regenerate /etc/cron.d/slugtube from config. Called after schedule changes."""
    if config is None:
        config = load_config()

    fast = get_schedule(config, "fast_cron", "0 */6 * * *")
    full = get_schedule(config, "full_cron", "0 3 * * 0")
    update = get_schedule(config, "update_cron", "0 1 * * *")

    fast_enabled = config.get("fast_enabled", True)
    full_enabled = config.get("full_enabled", True)
    update_enabled = config.get("update_enabled", True)

    tz = get_timezone()

    lines = [
        "SHELL=/bin/bash",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        f"CRON_TZ={tz}",
        "",
    ]

    if fast_enabled:
        lines.append(f"{fast} root /app/scripts/download.sh --fast >> /config/logs/slugtube.log 2>&1")
    else:
        lines.append(f"# DISABLED: {fast} root /app/scripts/download.sh --fast >> /config/logs/slugtube.log 2>&1")

    if full_enabled:
        lines.append(f"{full} root /app/scripts/download.sh --full >> /config/logs/slugtube.log 2>&1")
    else:
        lines.append(f"# DISABLED: {full} root /app/scripts/download.sh --full >> /config/logs/slugtube.log 2>&1")

    if update_enabled:
        lines.append(f"{update} root /app/scripts/update-ytdlp.sh >> /config/logs/slugtube.log 2>&1")
    else:
        lines.append(f"# DISABLED: {update} root /app/scripts/update-ytdlp.sh >> /config/logs/slugtube.log 2>&1")

    lines.append("")  # trailing newline required by cron

    cron_path = "/etc/cron.d/slugtube"
    try:
        with open(cron_path, "w") as f:
            f.write("\n".join(lines))
        os.chmod(cron_path, 0o644)
        return True
    except Exception as e:
        print(f"⚠️ Failed to write cron: {e}")
        return False


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"last_fast": None, "last_full": None, "total_downloaded": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _is_ajax():
    """Return True if the request came from an AJAX/fetch call."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _ajax_or_redirect(msg, fallback="channels", status="ok"):
    """Return JSON for AJAX requests, redirect for normal form posts."""
    if _is_ajax():
        return jsonify({"status": status, "message": msg})
    return redirect(request.referrer or url_for(fallback))


def is_paused():
    return os.path.isfile(PAUSE_FILE)


def set_paused(paused):
    if paused:
        Path(PAUSE_FILE).touch()
    else:
        try:
            os.remove(PAUSE_FILE)
        except FileNotFoundError:
            pass


def count_lines(filepath):
    try:
        with open(filepath, "r") as f:
            return sum(1 for line in f)
    except FileNotFoundError:
        return 0


def get_channels():
    channels = []
    current_section = "Unsorted"
    try:
        with open(CHANNELS_FILE, "r") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("# ---") and "---" in line[5:]:
                    current_section = line.strip("# -").strip()
                elif line.startswith("#") or line.strip() == "":
                    continue
                else:
                    raw = line.strip()
                    index_playlists = False
                    # Handle pipe separator: URL|DisplayName or URL|DisplayName|playlists
                    if "|" in raw:
                        parts = raw.split("|")
                        url = parts[0].strip()
                        name = parts[1].strip() if len(parts) > 1 else url
                        # Skip malformed entries (empty URL)
                        if not url:
                            continue
                        # Check for |playlists flag (third field or any field after name)
                        for extra in parts[2:]:
                            if extra.strip().lower() == 'playlists':
                                index_playlists = True
                    else:
                        url = raw
                        name = url
                        if "/@" in url:
                            name = url.split("/@")[1].split("/")[0]
                        elif "/c/" in url:
                            name = url.split("/c/")[1].split("/")[0]
                        elif "playlist?list=" in url:
                            name = url.split("list=")[1][:20]
                        elif "/channel/" in url:
                            name = url.split("/channel/")[1].split("/")[0][:20]
                    # Clean up URL-encoded names
                    name = name.replace("%20", " ").replace("+", " ")
                    channels.append({"url": url, "name": name, "section": current_section, "type": "channel", "index_playlists": index_playlists})
    except FileNotFoundError:
        pass
    return channels


def _scan_channel_stats():
    """Background scanner — populates cache without blocking page loads."""
    _stats_cache["scanning"] = True
    stats = []
    try:
        shows = Path(SHOWS_DIR)
        if not shows.exists():
            _stats_cache["data"] = []
            _stats_cache["scanning"] = False
            return
        for channel_dir in sorted(shows.iterdir()):
            if not channel_dir.is_dir():
                continue
            # Skip TubeArchivist metadata dirs (e.g. "Channel#playlists")
            if channel_dir.name.endswith('#playlists'):
                continue
            # Auto-merge Channel# folders into base Channel folder
            if channel_dir.name.endswith('#'):
                import shutil
                base_name = channel_dir.name.rstrip('#')
                hash_name = channel_dir.name
                base_path = channel_dir.parent / base_name
                if base_path.is_dir():
                    # Both exist: merge files from # into base. Files whose
                    # destination already exists are LEFT IN PLACE (never
                    # deleted) — only empty directories are removed after.
                    leftovers = 0
                    for root, dirs, files in os.walk(str(channel_dir)):
                        rel_root = os.path.relpath(root, str(channel_dir))
                        dest_root = base_path / rel_root
                        for f in files:
                            src = Path(root) / f
                            dest = dest_root / f
                            if not dest.exists():
                                dest_root.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(src), str(dest))
                            else:
                                leftovers += 1
                    # Remove now-empty dirs only; a non-empty # folder stays
                    for root, dirs, files in os.walk(str(channel_dir), topdown=False):
                        try:
                            os.rmdir(root)
                        except OSError:
                            pass
                    if leftovers:
                        app.logger.warning(
                            f"Merge {hash_name} → {base_name}: {leftovers} file(s) "
                            f"collided with existing files and were left in "
                            f"{hash_name}/ for manual review (nothing deleted)")
                    else:
                        app.logger.info(f"Auto-merged and removed: {hash_name} → {base_name}")
                else:
                    # Only # exists: just rename it
                    try:
                        channel_dir.rename(base_path)
                        app.logger.info(f"Auto-renamed: {hash_name} → {base_name}")
                    except Exception as e:
                        app.logger.warning(f"Failed to rename {hash_name}: {e}")
                        continue
                # DB cleanup: reassign videos and remove ghost channel entry
                try:
                    from indexer import get_db as idx_get_db
                    conn = idx_get_db()
                    conn.execute("UPDATE videos SET channel_name = ? WHERE channel_name = ?", (base_name, hash_name))
                    # Fix ALL path columns — leaving them pointing into the
                    # deleted # folder is what caused missing thumbnails and
                    # unplayable merged videos.
                    old_prefix = str(channel_dir.parent / hash_name) + "/"
                    new_prefix = str(base_path) + "/"
                    for col in ("file_path", "thumbnail_path", "subtitle_path", "json_path"):
                        conn.execute(
                            f"UPDATE videos SET {col} = ? || substr({col}, ?) "
                            f"WHERE {col} LIKE ? || '%'",
                            (new_prefix, len(old_prefix) + 1, old_prefix))
                    conn.execute("UPDATE playlists SET channel_name = ? WHERE channel_name = ?", (base_name, hash_name))
                    conn.execute("UPDATE channels SET name = ?, path = ? WHERE name = ?",
                                 (base_name, str(base_path), hash_name))
                    # If base channel already existed in DB, remove the duplicate
                    rows = conn.execute("SELECT COUNT(*) FROM channels WHERE name = ?", (base_name,)).fetchone()[0]
                    if rows > 1:
                        conn.execute("DELETE FROM channels WHERE name = ? AND rowid NOT IN (SELECT MIN(rowid) FROM channels WHERE name = ?)", (base_name, base_name))
                    conn.commit()
                    conn.close()
                    app.logger.info(f"DB cleanup: {hash_name} → {base_name}")
                except Exception as e:
                    app.logger.warning(f"DB cleanup failed for {hash_name}: {e}")
                continue
            video_count = 0
            total_size = 0
            seasons = []
            for item in channel_dir.iterdir():
                if item.is_dir() and item.name.startswith("Season"):
                    season_vids = 0
                    for f in item.iterdir():
                        if f.suffix in ('.mp4', '.mkv', '.webm'):
                            video_count += 1
                            season_vids += 1
                            try:
                                total_size += f.stat().st_size
                            except:
                                pass
                    if season_vids > 0:
                        seasons.append({"name": item.name, "count": season_vids})
            has_poster = (channel_dir / "poster.jpg").exists()
            has_nfo = (channel_dir / "tvshow.nfo").exists()
            stats.append({
                "name": channel_dir.name,
                "path": str(channel_dir),
                "videos": video_count,
                "size_gb": round(total_size / (1024**3), 2),
                "seasons": sorted(seasons, key=lambda s: s["name"]),
                "season_count": len(seasons),
                "has_poster": has_poster,
                "has_nfo": has_nfo,
            })
    except Exception as e:
        print(f"Error scanning channels: {e}")
    import time
    _stats_cache["data"] = stats
    _stats_cache["updated"] = time.time()
    _stats_cache["scanning"] = False
    print(f"📊 Library scan complete: {len(stats)} channels, {sum(c['videos'] for c in stats)} videos")


def get_channel_stats():
    """Returns cached stats instantly. Triggers background scan if stale."""
    import time
    now = time.time()
    if (now - _stats_cache["updated"]) >= STATS_CACHE_TTL and not _stats_cache["scanning"]:
        thread = threading.Thread(target=_scan_channel_stats, daemon=True)
        thread.start()
    return _stats_cache["data"]


def _get_playlist_channels():
    """Return list of channel dicts that have the |playlists flag set."""
    return [ch for ch in get_channels() if ch.get('index_playlists')]


def _trigger_background_index(force=False):
    """Start background index, automatically including playlist channels."""
    from indexer import start_background_index
    pl_channels = _get_playlist_channels()
    start_background_index(force=force, playlist_channels=pl_channels if pl_channels else None)


def get_disk_usage():
    """Use df (instant) instead of du (scans every file — hangs on 16TB)."""
    try:
        result = subprocess.run(["df", "-h", SHOWS_DIR], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            return f"{parts[2]} / {parts[1]}"
        return "N/A"
    except:
        return "N/A"


def get_log_tail(lines=100, filter_mode=None):
    try:
        if lines == 0:
            # Full log
            with open(LOG_FILE, 'r') as f:
                text = f.read()
        else:
            result = subprocess.run(["tail", "-n", str(lines), LOG_FILE], capture_output=True, text=True, timeout=5)
            text = result.stdout

        # Apply filter
        if filter_mode and text:
            filtered_lines = []
            for line in text.split('\n'):
                if filter_mode == 'errors':
                    if any(kw in line.lower() for kw in ['error', '❌', 'fail', 'traceback', 'exception', 'warning']):
                        if not any(skip in line.lower() for skip in ['has already been downloaded', 'skipping', 'is not a video']):
                            filtered_lines.append(line)
                elif filter_mode == 'downloads':
                    if any(kw in line for kw in ['✅', 'Downloading', '[download]', 'Merged', 'Downloaded']):
                        filtered_lines.append(line)
                elif filter_mode == 'skipped':
                    if any(kw in line for kw in ['⏭️', 'already been downloaded', 'Skipping', 'skipping']):
                        filtered_lines.append(line)
            text = '\n'.join(filtered_lines)

        return text
    except:
        return "No logs yet."



# ── Tiny TTL cache for per-page-load hot spots ─────────────────────────
# The dashboard used to grep the ENTIRE download log, spawn a yt-dlp
# subprocess, parse the whole cookie jar, and count the whole archive
# file on EVERY page load. With a 57k-line archive and a multi-MB log
# (often over SMB), that made every dashboard render crawl.
_ttl_cache = {}
_ttl_lock = threading.Lock()

def _cached(key, ttl, fn):
    import time as _t
    now = _t.time()
    with _ttl_lock:
        hit = _ttl_cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    val = fn()
    with _ttl_lock:
        _ttl_cache[key] = (now, val)
    return val

def _cache_bust(key):
    with _ttl_lock:
        _ttl_cache.pop(key, None)

def _recent_downloads_uncached(count=20):
    """Read only the tail of the log (last 256KB) instead of grepping the
    whole multi-MB file on every page load."""
    downloads = []
    try:
        import re as _re
        pattern = _re.compile(r"(Downloading|Already|\[download\]|✅|⏭️|❌|🆕)")
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 262144))
            tail = f.read().decode("utf-8", errors="replace")
        matched = [ln.strip() for ln in tail.splitlines() if pattern.search(ln)]
        downloads = matched[-count:]
    except Exception:
        pass
    return downloads


def get_recent_downloads(count=20):
    return _cached(f"recent:{count}", 15, lambda: _recent_downloads_uncached(count))


def _ytdlp_version_uncached():
    try:
        ver = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5)
        return ver.stdout.strip()
    except:
        return "unknown"


def get_ytdlp_version():
    # Spawning a subprocess per page load added ~1s to every render
    return _cached("ytdlp_version", 3600, _ytdlp_version_uncached)


def _cookie_health_uncached():
    """Check cookie file health: expiration, age, existence, AND YouTube rejection signals from logs."""
    cookie_path = "/config/Cookie/cookies.txt"
    if not os.path.isfile(cookie_path):
        return {"status": "missing", "message": "No cookie file found", "age_days": 0, "expired_count": 0, "total_count": 0}

    try:
        stat = os.stat(cookie_path)
        file_age_days = (time.time() - stat.st_mtime) / 86400
        now = time.time()
        total = 0
        expired = 0

        with open(cookie_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    total += 1
                    try:
                        expires = int(parts[4])
                        if expires > 0 and expires < now:
                            expired += 1
                    except (ValueError, IndexError):
                        pass

        if total == 0:
            return {"status": "warning", "message": "Cookie file has no valid entries", "age_days": round(file_age_days, 1), "expired_count": 0, "total_count": 0}

        if expired > total * 0.5:
            return {"status": "expired", "message": f"{expired}/{total} cookies expired", "age_days": round(file_age_days, 1), "expired_count": expired, "total_count": total}

        # Check logs for YouTube server-side rejection (cookies look fine on paper but YouTube says no)
        rejection = _check_logs_for_cookie_rejection(file_age_days)
        if rejection:
            return rejection

        if file_age_days > 7:
            return {"status": "warning", "message": f"Cookie file is {round(file_age_days)} days old — consider refreshing", "age_days": round(file_age_days, 1), "expired_count": expired, "total_count": total}

        return {"status": "ok", "message": f"Cookies OK ({round(file_age_days, 1)} days old)", "age_days": round(file_age_days, 1), "expired_count": expired, "total_count": total}

    except Exception as e:
        return {"status": "missing", "message": f"Error reading cookies: {e}", "age_days": 0, "expired_count": 0, "total_count": 0}


def check_cookie_health():
    # Parsing the whole cookie jar on every page load is wasteful — 60s TTL
    return _cached("cookie_health", 60, _cookie_health_uncached)


def _check_logs_for_cookie_rejection(file_age_days=0):
    """Scan recent logs for signs YouTube is rejecting cookies, even if the file looks fine.
    
    Only flags errors that occurred AFTER the cookie file was last saved.
    This prevents stale log entries from keeping the dot red after a cookie refresh.
    """
    import re
    from datetime import datetime

    # Patterns that indicate YouTube is rejecting/ignoring cookies
    REJECTION_PATTERNS = [
        "cookies are no longer valid",
        "UNEXPECTED_EOF_WHILE_READING",
        "SSL: UNEXPECTED_EOF",
        "unable to download webpage",
        "Sign in to confirm your age",
        "Sign in to confirm you",
        "This video requires authentication",
        "HTTP Error 403",
        "HTTP Error 429",
        "IncompleteRead",
        "got an empty response",
    ]

    try:
        if not os.path.isfile(LOG_FILE):
            return None

        # Get cookie file modification time — errors BEFORE this don't count
        cookie_path = "/config/Cookie/cookies.txt"
        cookie_mtime = None
        if os.path.isfile(cookie_path):
            cookie_mtime = os.path.getmtime(cookie_path)

        with open(LOG_FILE, "r", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            # Read last 100KB to catch recent errors
            f.seek(max(0, size - 100000))
            recent = f.read()

        if not recent:
            return None

        recent_lower = recent.lower()

        # Count how many different rejection signals we see
        signals_found = []
        for pattern in REJECTION_PATTERNS:
            if pattern.lower() in recent_lower:
                signals_found.append(pattern)

        if not signals_found:
            return None

        # Check if errors are BOTH recent (within 2 hours) AND after cookie refresh
        now_ts = time.time()
        is_recent_and_post_refresh = False

        # Try to find timestamps in format "YYYY-MM-DD HH:MM:SS" near errors
        for pattern in signals_found[:3]:  # Check first few
            escaped = re.escape(pattern)
            matches = re.findall(
                r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}).*?" + escaped,
                recent, re.IGNORECASE | re.DOTALL
            )
            if matches:
                try:
                    last_time = datetime.strptime(matches[-1].replace("T", " "), "%Y-%m-%d %H:%M:%S")
                    last_time_ts = last_time.timestamp()
                    age_hours = (datetime.utcnow() - last_time).total_seconds() / 3600

                    # Must be recent AND after the cookie file was saved
                    if age_hours < 2:
                        if cookie_mtime is None or last_time_ts > cookie_mtime:
                            is_recent_and_post_refresh = True
                            break
                        # else: error is from before cookies were refreshed — ignore it
                except (ValueError, TypeError):
                    pass

        # Fallback: if no parseable timestamps, only flag if log was written
        # AFTER cookies AND within last hour (both conditions required)
        if not is_recent_and_post_refresh:
            try:
                log_mtime = os.path.getmtime(LOG_FILE)
                log_age_hours = (now_ts - log_mtime) / 3600
                if log_age_hours < 1:
                    # Log is fresh, but only flag if cookies are OLDER than the log
                    # (meaning errors happened after the last cookie save)
                    if cookie_mtime is not None and log_mtime > cookie_mtime:
                        # Log written after cookies — but we can't tell if the ERRORS
                        # are post-refresh without timestamps. Give cookies the benefit
                        # of the doubt for the first 10 minutes after refresh.
                        cookie_age_min = (now_ts - cookie_mtime) / 60
                        if cookie_age_min > 10:
                            is_recent_and_post_refresh = True
                    elif cookie_mtime is None:
                        is_recent_and_post_refresh = True
            except OSError:
                pass

        if not is_recent_and_post_refresh:
            return None

        # Determine severity based on which signals we see
        hard_reject = ["cookies are no longer valid", "HTTP Error 403", "Sign in to confirm"]
        ssl_errors = ["UNEXPECTED_EOF_WHILE_READING", "SSL: UNEXPECTED_EOF", "IncompleteRead"]

        has_hard_reject = any(p.lower() in recent_lower for p in hard_reject)
        has_ssl_errors = any(p.lower() in recent_lower for p in ssl_errors)

        if has_hard_reject:
            return {
                "status": "rejected",
                "message": f"YouTube rejected cookies — refresh from browser",
                "age_days": round(file_age_days, 1),
                "expired_count": 0,
                "total_count": 0,
                "signals": signals_found[:3]
            }
        elif has_ssl_errors:
            # SSL errors could be rate limiting OR cookie issues
            # Count how many SSL errors in recent log
            ssl_count = recent_lower.count("unexpected_eof")
            if ssl_count >= 3:
                return {
                    "status": "rejected",
                    "message": f"YouTube blocking connections ({ssl_count} SSL errors) — refresh cookies",
                    "age_days": round(file_age_days, 1),
                    "expired_count": 0,
                    "total_count": 0,
                    "signals": signals_found[:3]
                }
            else:
                return {
                    "status": "warning",
                    "message": f"Some YouTube connection errors — may need cookie refresh",
                    "age_days": round(file_age_days, 1),
                    "expired_count": 0,
                    "total_count": 0,
                    "signals": signals_found[:3]
                }

        # Generic rejection with multiple signals
        if len(signals_found) >= 2:
            return {
                "status": "warning",
                "message": f"YouTube may be rejecting cookies ({len(signals_found)} warning signs)",
                "age_days": round(file_age_days, 1),
                "expired_count": 0,
                "total_count": 0,
                "signals": signals_found[:3]
            }

        return None

    except Exception:
        return None


def run_single_channel(url, folder_name=None, fast=False):
    """Download a single channel. fast=True limits to latest 50 videos."""
    if is_paused():
        jobs["status"] = "paused"
        jobs["log"] = "Downloads are paused. Resume from the dashboard."
        return

    jobs["status"] = "running"
    jobs["mode"] = "fast-single" if fast else "single"
    jobs["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cmd = ["/app/scripts/download.sh", "--fast-single" if fast else "--single", url]
        if folder_name:
            cmd.extend(["--folder", folder_name])

        # Stream output in real-time to log file + jobs["log"]
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        log_lines = []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(LOG_FILE, "a") as logf:
            logf.write(f"\n{ts} [download] 🐌 Single channel download: {url}\n")
            if os.environ.get("SLUGTUBE_DEBUG") == "1":
                logf.write(f"{ts} [download] [DEBUG] folder_name={folder_name!r}\n")
                logf.write(f"{ts} [download] [DEBUG] cmd={cmd!r}\n")
            if folder_name:
                logf.write(f"{ts} [download] 📁 Folder: {folder_name}\n")
            logf.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            jobs["proc"] = proc
            for line in proc.stdout:
                line = line.rstrip('\n')
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logf.write(f"{ts} [download] {line}\n")
                logf.flush()
                log_lines.append(line)
                # Keep only last 200 lines in memory for dashboard
                if len(log_lines) > 200:
                    log_lines.pop(0)
                jobs["log"] = "\n".join(log_lines[-100:])

            proc.wait()
            status = "✅ Done" if proc.returncode == 0 else f"❌ Error (exit {proc.returncode})"
            logf.write(f"{ts} [download] {status}\n")
            logf.flush()

        jobs["log"] = "\n".join(log_lines[-100:]) if log_lines else "No output"
        jobs["status"] = "done" if proc.returncode in (0, 101) else "error"

        # Trigger re-index after downloads complete
        try:
            _trigger_background_index()
        except:
            pass
    except subprocess.TimeoutExpired:
        jobs["status"] = "timeout"
        jobs["log"] = "Job timed out after 4 hours"
    except Exception as e:
        jobs["status"] = "error"
        jobs["log"] = str(e)


def run_download(mode):
    if is_paused():
        jobs["status"] = "paused"
        jobs["log"] = "Downloads are paused. Resume from the dashboard."
        return

    jobs["status"] = "running"
    jobs["mode"] = mode
    jobs["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.Popen(
            ["/app/scripts/download.sh", f"--{mode}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        jobs["proc"] = proc

        log_lines = []
        log_file = open(LOG_FILE, "a") if LOG_FILE else None
        for line in proc.stdout:
            line = line.rstrip('\n')
            log_lines.append(line)
            if len(log_lines) > 200:
                log_lines = log_lines[-200:]
            jobs["log"] = '\n'.join(log_lines)
            if log_file:
                log_file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [download] {line}\n")
                log_file.flush()

        proc.wait()
        if log_file:
            log_file.close()

        jobs["status"] = "done" if proc.returncode in (0, 101) else "error"
        state = load_state()
        state[f"last_{mode}"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        # Trigger re-index after downloads complete
        try:
            _trigger_background_index()
        except:
            pass
    except Exception as e:
        jobs["status"] = "error"
        jobs["log"] = str(e)


def run_update():
    jobs["status"] = "running"
    jobs["mode"] = "update"
    jobs["started"] = datetime.now().strftime("%H:%M:%S")
    try:
        result = subprocess.run(["/app/scripts/update-ytdlp.sh"], capture_output=True, text=True, timeout=120)
        jobs["log"] = result.stdout if result.stdout else "Updated"
        jobs["status"] = "done"
        _cache_bust("ytdlp_version")
    except Exception as e:
        jobs["status"] = "error"
        jobs["log"] = str(e)


@app.route("/api/self-update", methods=["POST"])
def api_self_update():
    """Pull latest SlugTube code from GitHub and overwrite /app/web and /app/scripts.
    Also merges new channels from the repo's channels.txt into local channels.txt."""
    import urllib.request
    import tarfile
    import io
    import shutil

    REPO = "bmcgarybot/slugtube-oss"
    BRANCH = "master"
    TARBALL_URL = f"https://api.github.com/repos/{REPO}/tarball/{BRANCH}"
    LOCAL_CHANNELS = "/config/channels.txt"

    try:
        # Download tarball from GitHub
        req = urllib.request.Request(TARBALL_URL, headers={"User-Agent": "SlugTube-Updater"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            tarball_data = resp.read()

        # Extract to temp dir
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tar = tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz")
            tar.extractall(tmpdir)
            tar.close()

            # GitHub tarballs extract to a single top-level dir like "bmcgarybot-slugtube-abc1234/"
            extracted_dirs = os.listdir(tmpdir)
            if not extracted_dirs:
                return jsonify({"status": "error", "message": "Empty tarball"}), 500
            repo_root = os.path.join(tmpdir, extracted_dirs[0])

            updated = []

            # Update /app/web
            src_web = os.path.join(repo_root, "web")
            if os.path.isdir(src_web):
                # Copy all files, overwriting existing
                for root, dirs, files in os.walk(src_web):
                    rel = os.path.relpath(root, src_web)
                    dest_dir = os.path.join("/app/web", rel)
                    os.makedirs(dest_dir, exist_ok=True)
                    for f in files:
                        shutil.copy2(os.path.join(root, f), os.path.join(dest_dir, f))
                updated.append("web")

            # Update /app/scripts
            src_scripts = os.path.join(repo_root, "scripts")
            if os.path.isdir(src_scripts):
                for root, dirs, files in os.walk(src_scripts):
                    rel = os.path.relpath(root, src_scripts)
                    dest_dir = os.path.join("/app/scripts", rel)
                    os.makedirs(dest_dir, exist_ok=True)
                    for f in files:
                        dest_file = os.path.join(dest_dir, f)
                        shutil.copy2(os.path.join(root, f), dest_file)
                        # Re-apply executable bit for shell scripts
                        if f.endswith('.sh'):
                            os.chmod(dest_file, 0o755)
                updated.append("scripts")

            # NOTE: self-update deliberately does NOT touch channels.txt or
            # anything else in /config. Code updates must never modify user
            # data - your subscriptions, library, history, and archive are
            # yours alone. (A merge feature used to live here; removed.)

        msg = f"Updated: {', '.join(updated)}. Refresh your browser (Ctrl+Shift+R)." if updated else "Already up to date."
        return _ajax_or_redirect(msg, fallback="settings")

    except Exception as e:
        if _is_ajax():
            return jsonify({"status": "error", "message": f"Update failed: {e}"}), 500
        return redirect(request.referrer or url_for("settings"))


SLUGTUBE_CODE_VERSION = "2026-05-29b"

# ── Original Routes ──────────────────────────────────────────────

@app.route("/")
def dashboard():
    channels = get_channels()
    archive_count = _cached("archive_count", 60, lambda: count_lines(ARCHIVE_FILE))
    disk_usage = get_disk_usage()
    state = load_state()
    config = load_config()
    cookie_health = check_cookie_health()

    # Use the indexer database for stats (same source as Library page)
    from indexer import get_library_stats, get_all_channels, get_index_status, get_recent_videos
    idx_stats = get_library_stats()
    idx_channels = get_all_channels()
    idx_status = get_index_status()
    total_videos = idx_stats.get('total_videos', 0)
    top_channels = sorted(idx_channels, key=lambda c: c.get('video_count', 0), reverse=True)[:10]
    recent_videos = get_recent_videos(limit=8)

    return render_template("dashboard.html",
        page="dashboard",
        channel_count=len(channels),
        archive_count=archive_count,
        total_videos=total_videos,
        library_count=idx_stats.get('total_channels', 0),
        disk_usage=disk_usage,
        ytdlp_version=get_ytdlp_version(),
        job=jobs,
        state=state,
        paused=is_paused(),
        config=config,
        recent=get_recent_downloads(15),
        top_channels=top_channels,
        recent_videos=recent_videos,
        scanning=idx_status.get('scanning', False),
        cookie_health=cookie_health,
    )


@app.route("/channels")
def channels():
    channels_list = get_channels()
    from indexer import get_all_channels, get_index_status
    idx_channels = get_all_channels()
    idx_status = get_index_status()

    # Auto-create folders for subscribed channels that don't have one yet
    for ch in channels_list:
        ch_dir = Path(SHOWS_DIR) / ch['name']
        if not ch_dir.is_dir():
            try:
                ch_dir.mkdir(parents=True, exist_ok=True)
                app.logger.info(f"Created folder for subscribed channel: {ch['name']}")
            except Exception as e:
                app.logger.error(f"Failed to create folder for {ch['name']}: {e}")

    # Auto-delete ghost TubeArchivist "#playlists" folders only
    # (NOT regular folders with # — yt-dlp creates channel folders like "Travel Channel#")
    try:
        import shutil
        shows = Path(SHOWS_DIR)
        if shows.is_dir():
            for d in shows.iterdir():
                if d.is_dir() and d.name.endswith('#playlists'):
                    shutil.rmtree(str(d), ignore_errors=True)
                    app.logger.info(f"Removed ghost folder: {d.name}")
    except Exception as e:
        app.logger.error(f"Ghost folder cleanup error: {e}")

    # Auto-purge ghost DB entries: channels whose folder no longer exists on disk
    from indexer import get_db as idx_get_db
    try:
        conn = idx_get_db()
        for ch in idx_channels:
            ch_path = ch.get('path', '')
            if ch_path and not os.path.isdir(ch_path):
                name = ch['name']
                app.logger.info(f"Auto-purging ghost channel: {name} (path gone: {ch_path})")
                video_ids = [r['id'] for r in conn.execute(
                    "SELECT id FROM videos WHERE channel_name = ?", (name,)
                ).fetchall()]
                if video_ids:
                    ph = ",".join("?" * len(video_ids))
                    conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({ph})", video_ids)
                    conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({ph})", video_ids)
                    conn.execute(f"DELETE FROM videos WHERE channel_name = ?", (name,))
                try:
                    conn.execute("DELETE FROM videos_fts WHERE channel_name = ?", (name,))
                except Exception:
                    pass
                conn.execute("DELETE FROM playlists WHERE channel_name = ?", (name,))
                conn.execute("DELETE FROM channels WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        # Refresh after cleanup
        idx_channels = get_all_channels()
    except Exception as e:
        app.logger.error(f"Ghost channel cleanup error: {e}")

    # Build unified channel list: merge subscriptions + library
    sub_names = {ch['name'].lower().replace(' ', ''): ch for ch in channels_list}
    lib_names = {ch['name'].lower().replace(' ', ''): ch for ch in idx_channels}

    unified = []
    seen = set()

    # Start with subscribed channels, merge in library stats
    for ch in channels_list:
        key = ch['name'].lower().replace(' ', '')
        entry = {
            'name': ch['name'],
            'url': ch['url'],
            'section': ch['section'],
            'index_playlists': ch.get('index_playlists', False),
            'subscribed': True,
            'on_disk': False,
            'video_count': 0,
            'total_size_bytes': 0,
            'has_nfo': False,
            'has_poster': False,
        }
        # Match with library data
        if key in lib_names:
            lib = lib_names[key]
            entry['on_disk'] = True
            entry['video_count'] = lib.get('video_count', 0)
            entry['total_size_bytes'] = lib.get('total_size_bytes', 0)
            entry['has_nfo'] = lib.get('has_nfo', False)
            entry['has_poster'] = lib.get('has_poster', False)
        else:
            # Try fuzzy match: check if subscription name appears in any library name
            for lkey, lib in lib_names.items():
                if key in lkey or lkey in key or ch['name'].lower() in lib['name'].lower() or lib['name'].lower() in ch['name'].lower():
                    entry['on_disk'] = True
                    entry['video_count'] = lib.get('video_count', 0)
                    entry['total_size_bytes'] = lib.get('total_size_bytes', 0)
                    entry['has_nfo'] = lib.get('has_nfo', False)
                    entry['has_poster'] = lib.get('has_poster', False)
                    seen.add(lkey)
                    break
        seen.add(key)
        unified.append(entry)

    # Add library-only channels (on disk but not subscribed)
    for ch in idx_channels:
        key = ch['name'].lower().replace(' ', '')
        # Skip # channel entries — these are yt-dlp artifacts that should be merged
        if ch['name'].endswith('#'):
            continue
        if key not in seen:
            # Check URL match too
            matched = False
            for sub in channels_list:
                if ch['name'].lower() in sub['url'].lower():
                    matched = True
                    break
            if not matched:
                unified.append({
                    'name': ch['name'],
                    'url': '',
                    'section': 'Library Only',
                    'index_playlists': False,
                    'subscribed': False,
                    'on_disk': True,
                    'video_count': ch.get('video_count', 0),
                    'total_size_bytes': ch.get('total_size_bytes', 0),
                    'has_nfo': ch.get('has_nfo', False),
                    'has_poster': ch.get('has_poster', False),
                })

    return render_template("channels.html",
        page="channels",
        channels=channels_list,
        unified=unified,
        channel_stats=idx_channels,
        job=jobs,
        paused=is_paused(),
        scanning=idx_status.get('scanning', False),
    )


@app.route("/logs")
def logs():
    lines = request.args.get("lines", 150, type=int)
    filter_mode = request.args.get("filter", "", type=str) or None
    log_text = get_log_tail(lines, filter_mode=filter_mode)
    log_size = 0
    try:
        log_size = os.path.getsize(LOG_FILE)
    except:
        pass
    return render_template("logs.html",
        page="logs",
        log_text=log_text,
        lines=lines,
        log_size=log_size,
        filter=filter_mode or '',
        job=jobs,
        paused=is_paused(),
    )


@app.route("/cookies")
def cookies():
    save_status = request.args.get("saved", None)
    cookie_path = "/config/Cookie/cookies.txt"
    cookie_exists = os.path.isfile(cookie_path)
    cookie_content = ""
    cookie_size = ""
    cookie_modified = ""
    if cookie_exists:
        try:
            stat = os.stat(cookie_path)
            cookie_size = f"{stat.st_size / 1024:.1f} KB"
            cookie_modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            with open(cookie_path, "r", errors="replace") as f:
                cookie_content = f.read()
        except Exception as e:
            cookie_content = f"Error reading file: {e}"
    return render_template("cookies.html",
        page="cookies",
        cookie_exists=cookie_exists,
        cookie_content=cookie_content,
        cookie_size=cookie_size,
        cookie_modified=cookie_modified,
        save_status=save_status,
        job=jobs,
        paused=is_paused(),
    )


@app.route("/cookies/save", methods=["POST"])
def save_cookies():
    cookie_text = request.form.get("cookies", "")
    cookie_path = "/config/Cookie/cookies.txt"
    try:
        os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

        # Fix tab-mangling: browsers often convert tabs to spaces in textareas
        fixed_lines = []
        tabs_fixed = 0
        for line in cookie_text.split("\n"):
            stripped = line.strip()
            # Skip comments and blank lines
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                fixed_lines.append(line)
                continue
            # If a data line has no tabs but has multi-spaces, fix it
            if "\t" not in line and "  " in line:
                # Cookie format: domain flag path secure expiry name value (7 fields)
                line = re.sub(r" {2,}", "\t", line)
                tabs_fixed += 1
            fixed_lines.append(line)

        cookie_text = "\n".join(fixed_lines)

        if tabs_fixed > 0:
            print(f"🍪 Cookie save: auto-fixed tabs in {tabs_fixed} lines (browser mangled them)")

        with open(cookie_path, "w") as f:
            f.write(cookie_text)
        return redirect("/cookies?saved=success")
    except Exception as e:
        print(f"Error saving cookies: {e}")
        return redirect("/cookies?saved=error")


@app.route("/settings")
def settings():
    config = load_config()
    fast_cron = get_schedule(config, "fast_cron", "0 */6 * * *")
    full_cron = get_schedule(config, "full_cron", "0 3 * * 0")
    update_cron = get_schedule(config, "update_cron", "0 1 * * *")
    state = load_state()
    save_status = request.args.get("saved", None)
    tz = get_timezone()
    current_time = datetime.now().strftime("%I:%M %p, %b %d")
    return render_template("settings.html",
        page="settings",
        fast_cron=fast_cron,
        full_cron=full_cron,
        update_cron=update_cron,
        fast_enabled=config.get("fast_enabled", True),
        full_enabled=config.get("full_enabled", True),
        update_enabled=config.get("update_enabled", True),
        schedule_presets=SCHEDULE_PRESETS,
        timezone=tz,
        timezones=get_timezone_list(),
        current_time=current_time,
        ytdlp_version=get_ytdlp_version(),
        archive_count=count_lines(ARCHIVE_FILE),
        state=state,
        config=config,
        save_status=save_status,
        job=jobs,
        paused=is_paused(),
    )


@app.route("/settings/save", methods=["POST"])
def save_settings():
    config = load_config()
    config["quality"] = request.form.get("quality", "1080p")
    config["format"] = request.form.get("format", "mp4")
    config["embed_subs"] = request.form.get("embed_subs") == "on"
    config["embed_metadata"] = request.form.get("embed_metadata") == "on"
    config["embed_thumbnail"] = request.form.get("embed_thumbnail") == "on"
    config["write_nfo"] = request.form.get("write_nfo") == "on"
    config["write_thumbnail"] = request.form.get("write_thumbnail") == "on"
    config["write_subs_file"] = request.form.get("write_subs_file") == "on"
    config["skip_shorts"] = request.form.get("skip_shorts") == "on"
    save_config(config)
    return redirect("/settings?saved=success")


@app.route("/api/schedule/save", methods=["POST"])
def save_schedule():
    """Save schedule settings and rebuild cron."""
    data = request.get_json() or {}
    config = load_config()

    # Update timezone if provided
    tz_changed = False
    if "timezone" in data and data["timezone"]:
        new_tz = data["timezone"]
        old_tz = get_timezone()
        if new_tz != old_tz:
            # Validate timezone exists
            if os.path.isfile(f"/usr/share/zoneinfo/{new_tz}"):
                config["timezone"] = new_tz
                apply_timezone(new_tz)
                tz_changed = True
                print(f"🕐 Timezone changed: {old_tz} → {new_tz}")
            else:
                return jsonify({"status": "error", "message": f"Invalid timezone: {new_tz}"}), 400

    # Update cron expressions
    if "fast_cron" in data:
        config["fast_cron"] = data["fast_cron"]
    if "full_cron" in data:
        config["full_cron"] = data["full_cron"]
    if "update_cron" in data:
        config["update_cron"] = data["update_cron"]

    # Update enabled/disabled
    if "fast_enabled" in data:
        config["fast_enabled"] = bool(data["fast_enabled"])
    if "full_enabled" in data:
        config["full_enabled"] = bool(data["full_enabled"])
    if "update_enabled" in data:
        config["update_enabled"] = bool(data["update_enabled"])

    save_config(config)
    success = rebuild_cron(config)

    current_time = datetime.now().strftime("%I:%M %p, %b %d")

    return jsonify({
        "status": "ok" if success else "error",
        "message": "Schedules updated!" if success else "Failed to write cron file",
        "fast_cron": get_schedule(config, "fast_cron", "0 */6 * * *"),
        "full_cron": get_schedule(config, "full_cron", "0 3 * * 0"),
        "update_cron": get_schedule(config, "update_cron", "0 1 * * *"),
        "fast_enabled": config.get("fast_enabled", True),
        "full_enabled": config.get("full_enabled", True),
        "update_enabled": config.get("update_enabled", True),
        "timezone": get_timezone(),
        "current_time": current_time,
    })


@app.route("/add", methods=["POST"])
def add_channel():
    url = request.form.get("url", "").strip()
    name = request.form.get("name", "").strip()
    playlists = request.form.get("playlists") == "on"
    if url and ("youtube.com" in url or "youtu.be" in url):
        entry = url
        if name:
            entry = f"{url}|{name}"
            if playlists:
                entry += "|playlists"
        elif playlists:
            # Need a name to add the playlists flag
            if "/@" in url:
                auto_name = url.split("/@")[1].split("/")[0]
            else:
                auto_name = url
            entry = f"{url}|{auto_name}|playlists"
        with open(CHANNELS_FILE, "a") as f:
            f.write(f"\n{entry}\n")
        # Create folder immediately so channel shows as Active
        folder_name = name
        if not folder_name:
            if "/@" in url:
                folder_name = url.split("/@")[1].split("/")[0]
            else:
                folder_name = url
        if folder_name:
            ch_dir = Path(SHOWS_DIR) / folder_name
            ch_dir.mkdir(parents=True, exist_ok=True)
    return redirect(request.referrer or url_for("channels"))


@app.route("/remove", methods=["POST"])
def remove_channel():
    # Record the removal so channel merges/updates NEVER silently re-add it
    # (mirrors the per-video delete-and-block behavior).
    try:
        _rm_url = (request.get_json(silent=True) or {}).get("url") or request.form.get("url", "")
        if _rm_url:
            with open(os.path.join(os.path.dirname(CHANNELS_FILE), "channels.removed.txt"), "a") as _bf:
                _bf.write(_rm_url.strip() + "\n")
    except Exception:
        pass
    url = request.form.get("url", "").strip()
    name = request.form.get("name", "").strip()

    # 1) Remove from channels.txt (if URL matches)
    if url:
        try:
            with open(CHANNELS_FILE, "r") as f:
                lines = f.readlines()
            with open(CHANNELS_FILE, "w") as f:
                for line in lines:
                    stripped = line.strip()
                    # Lines are "URL|FolderName" — match against URL portion
                    line_url = stripped.split("|")[0].strip()
                    if stripped and line_url != url and stripped != url:
                        f.write(line)
        except FileNotFoundError:
            pass

    # 2) Purge from database (by name) — kills ghost entries the indexer left behind
    if name:
        try:
            conn = get_db()
            # Delete videos, FTS entries, playlists, watch history, then the channel
            video_ids = [r['id'] for r in conn.execute(
                "SELECT id FROM videos WHERE channel_name = ?", (name,)
            ).fetchall()]
            if video_ids:
                placeholders = ",".join("?" * len(video_ids))
                conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({placeholders})", video_ids)
                conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({placeholders})", video_ids)
                conn.execute(f"DELETE FROM videos_fts WHERE channel_name = ?", (name,))
                conn.execute(f"DELETE FROM videos WHERE channel_name = ?", (name,))
            conn.execute("DELETE FROM playlists WHERE channel_name = ?", (name,))
            conn.execute("DELETE FROM channels WHERE name = ?", (name,))
            conn.commit()
        except Exception as e:
            print(f"[remove_channel] DB cleanup error: {e}")

    # 3) Delete empty folder from disk (prevents ghost re-indexing)
    if name:
        import shutil
        channel_dir = Path(SHOWS_DIR) / name
        if channel_dir.is_dir():
            # Only auto-delete if folder has no video files (empty or just metadata)
            video_extensions = {'.mp4', '.mkv', '.webm', '.avi', '.mov'}
            has_videos = False
            for root, dirs, files in os.walk(str(channel_dir)):
                for f in files:
                    if os.path.splitext(f)[1].lower() in video_extensions:
                        has_videos = True
                        break
                if has_videos:
                    break
            if not has_videos:
                try:
                    shutil.rmtree(str(channel_dir))
                    print(f"[remove_channel] Deleted empty folder: {channel_dir}")
                except Exception as e:
                    print(f"[remove_channel] Failed to delete folder {channel_dir}: {e}")

    return _ajax_or_redirect(f"Unsubscribed from {name or 'channel'}")


@app.route("/pause", methods=["POST"])
def pause():
    set_paused(True)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/resume", methods=["POST"])
def resume():
    set_paused(False)
    jobs["status"] = "idle"
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/run/cancel", methods=["POST"])
def cancel_all():
    """Kill running download and clear the queue."""
    import signal, glob

    cancelled = []

    # 1. Kill running subprocess
    proc = jobs.get("proc")
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except Exception:
                pass
        cancelled.append(f"Killed running download (PID {proc.pid})")

    # 2. Kill any download.sh by lock file PID
    lock_file = "/config/slugtube.lock"
    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                lock_pid = int(f.read().strip())
            os.kill(lock_pid, signal.SIGTERM)
            cancelled.append(f"Killed lock holder (PID {lock_pid})")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        try:
            os.remove(lock_file)
        except OSError:
            pass

    # 3. Clear the queue
    queue_dir = "/config/queue"
    queued_files = glob.glob(os.path.join(queue_dir, "*.job"))
    for qf in queued_files:
        try:
            os.remove(qf)
        except OSError:
            pass
    if queued_files:
        cancelled.append(f"Cleared {len(queued_files)} queued job(s)")

    # 4. Reset job state
    jobs["status"] = "idle"
    jobs["mode"] = None
    jobs["proc"] = None
    jobs["log"] = "⛔ Cancelled. " + " | ".join(cancelled) if cancelled else "Nothing was running."

    return redirect(request.referrer or url_for("dashboard"))


@app.route("/run/<mode>", methods=["POST"])
def run(mode):
    if mode == "update":
        if jobs["status"] != "running":
            thread = threading.Thread(target=run_update, daemon=True)
            thread.start()
        return redirect(request.referrer or url_for("settings"))

    if mode == "banners":
        if jobs["status"] != "running":
            def _run_banners():
                jobs["status"] = "running"
                jobs["mode"] = "banners"
                jobs["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                try:
                    result = subprocess.run(["/app/scripts/fetch-banners.sh"],
                        capture_output=True, text=True, timeout=3600)
                    jobs["log"] = result.stdout[-3000:] if result.stdout else "Done"
                    jobs["status"] = "done"
                except subprocess.TimeoutExpired:
                    jobs["status"] = "timeout"
                    jobs["log"] = "Banner fetch timed out"
                except Exception as e:
                    jobs["status"] = "error"
                    jobs["log"] = str(e)
            thread = threading.Thread(target=_run_banners, daemon=True)
            thread.start()
        return redirect(request.referrer or url_for("settings"))

    if mode not in ("fast", "full"):
        return "Invalid mode", 400
    if is_paused():
        set_paused(False)

    if jobs["status"] == "running":
        # Queue via download.sh's lock/queue system instead of silently dropping
        def _queue_job():
            proc = subprocess.Popen(
                ["/app/scripts/download.sh", f"--{mode}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            output = proc.communicate()[0]
            # Log the queue message
            if output:
                try:
                    with open(LOG_FILE, "a") as f:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for line in output.strip().split('\n'):
                            f.write(f"{ts} [queue] {line}\n")
                except Exception:
                    pass
        thread = threading.Thread(target=_queue_job, daemon=True)
        thread.start()
        return redirect(request.referrer or url_for("dashboard"))

    thread = threading.Thread(target=run_download, args=(mode,), daemon=True)
    thread.start()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/run/single", methods=["POST"])
def run_single():
    url = request.form.get("url", "").strip()
    folder_name = request.form.get("folder", "").strip() or None

    # If no folder from form, look it up in channels.txt
    if not folder_name and url:
        for ch in get_channels():
            if ch["url"] == url:
                folder_name = ch["name"]
                break

    if not url:
        return "Missing URL", 400
    if jobs["status"] == "running":
        # Queue via download.sh lock/queue system
        def _queue_single():
            cmd = ["/app/scripts/download.sh", "--single", url]
            if folder_name:
                cmd.extend(["--folder", folder_name])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            output = proc.communicate()[0]
            if output:
                try:
                    with open(LOG_FILE, "a") as f:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for line in output.strip().split('\n'):
                            f.write(f"{ts} [queue] {line}\n")
                except Exception:
                    pass
        thread = threading.Thread(target=_queue_single, daemon=True)
        thread.start()
        return _ajax_or_redirect(f"Queued download for {folder_name or 'channel'}...")
    if is_paused():
        set_paused(False)
    thread = threading.Thread(target=run_single_channel, args=(url, folder_name), daemon=True)
    thread.start()
    return _ajax_or_redirect(f"Downloading {folder_name or 'channel'}...")


@app.route("/run/fast-single", methods=["POST"])
def run_fast_single():
    url = request.form.get("url", "").strip()
    folder_name = request.form.get("folder", "").strip() or None

    if not folder_name and url:
        for ch in get_channels():
            if ch["url"] == url:
                folder_name = ch["name"]
                break

    if not url:
        return "Missing URL", 400
    if jobs["status"] == "running":
        # Queue via download.sh lock/queue system
        def _queue_fast_single():
            cmd = ["/app/scripts/download.sh", "--fast-single", url]
            if folder_name:
                cmd.extend(["--folder", folder_name])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            output = proc.communicate()[0]
            if output:
                try:
                    with open(LOG_FILE, "a") as f:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for line in output.strip().split('\n'):
                            f.write(f"{ts} [queue] {line}\n")
                except Exception:
                    pass
        thread = threading.Thread(target=_queue_fast_single, daemon=True)
        thread.start()
        return _ajax_or_redirect(f"Queued quick check for {folder_name or 'channel'}...")
    if is_paused():
        set_paused(False)
    thread = threading.Thread(target=run_single_channel, args=(url, folder_name, True), daemon=True)
    thread.start()
    return _ajax_or_redirect(f"Checking {folder_name or 'channel'} for new videos...")


@app.route("/logs/export")
def export_logs():
    """Download the full log file as a .txt file."""
    if os.path.exists(LOG_FILE):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(LOG_FILE, as_attachment=True,
                         download_name=f"slugtube-log-{ts}.txt",
                         mimetype="text/plain")
    return "No log file found.", 404


@app.route("/logs/clear", methods=["POST"])
def clear_logs():
    try:
        with open(LOG_FILE, "w") as f:
            f.truncate(0)
    except Exception as e:
        print(f"Error clearing logs: {e}")
    # Also clear the in-memory job status so the error banner goes away
    if jobs["status"] != "running":
        jobs["status"] = "idle"
        jobs["log"] = ""
        jobs["mode"] = None
    return redirect(url_for("logs"))


@app.route("/history/clear", methods=["POST"])
def clear_history():
    try:
        from indexer import get_db
        conn = get_db()
        conn.execute("DELETE FROM watch_history")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error clearing history: {e}")
    return redirect(url_for("watch_history"))


@app.route("/api/status")
def api_status():
    return jsonify({**jobs, "paused": is_paused()})


@app.route("/api/cookie-health")
def api_cookie_health():
    health = check_cookie_health()
    return jsonify(health)


@app.route("/api/log-size")
def api_log_size():
    try:
        size = os.path.getsize(LOG_FILE)
    except:
        size = 0
    return jsonify({"size_bytes": size})


@app.route("/api/refresh-stats", methods=["POST"])
def api_refresh_stats():
    if not _stats_cache["scanning"]:
        _stats_cache["updated"] = 0  # force stale
        thread = threading.Thread(target=_scan_channel_stats, daemon=True)
        thread.start()
        return jsonify({"status": "started"})
    return jsonify({"status": "already scanning"})


# ── Archive Audit endpoints ──
try:
    from archive_audit import register_audit_routes
    register_audit_routes(app, ARCHIVE_FILE, "/shows")
except ImportError:
    pass  # archive_audit.py not present, skip


@app.route("/api/health")
def api_health():
    """Identity endpoint so ArrBox can VERIFY this is SlugTube rather than
    just seeing something on port 5000."""
    return jsonify({"app": "slugtube", "ok": True})


@app.route("/api/sync-archive", methods=["POST"])
def api_sync_archive():
    """Backfill the yt-dlp archive with YouTube IDs from the indexer DB and
    from video files on disk. Non-destructive - only appends.

    Sources (union):
      1. The indexer database - the source of truth for everything SlugTube
         has cataloged, regardless of filename style.
      2. Filenames containing [VIDEOID] anywhere (yt-dlp style).
      3. Bare-ID filenames like VIDEOID.mp4 (TubeArchivist-import style).
    YouTube IDs are EXACTLY 11 chars of [A-Za-z0-9_-]; the old 8-15 char
    pattern both missed real IDs and added junk like [HDTV-1080p]."""
    import re as _re
    shows_dir = "/shows"
    archive_path = ARCHIVE_FILE

    existing_ids = set()
    try:
        with open(archive_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                existing_ids.add(parts[1] if len(parts) >= 2 else parts[0])
    except FileNotFoundError:
        pass

    ID = r"[A-Za-z0-9_-]{11}"
    bracket_re = _re.compile(r"\[(" + ID + r")\]")
    bare_re = _re.compile(r"^(" + ID + r")$")
    VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v")

    found_ids = set()
    scanned = 0

    # 1) Indexer DB - the real catalog
    db_ids = 0
    try:
        from indexer import get_db
        conn = get_db()
        for row in conn.execute("SELECT video_id FROM videos WHERE video_id IS NOT NULL"):
            vid = (row[0] or "").strip()
            if _re.fullmatch(ID, vid):
                found_ids.add(vid)
        db_ids = len(found_ids)
        conn.close()
    except Exception:
        pass

    # 2 + 3) Filesystem scan
    for root, dirs, files in os.walk(shows_dir):
        for fname in files:
            if not fname.endswith(VIDEO_EXTS):
                continue
            scanned += 1
            stem = os.path.splitext(fname)[0]
            m = bracket_re.search(stem)
            if m:
                found_ids.add(m.group(1))
                continue
            m = bare_re.match(stem)
            if m:
                found_ids.add(m.group(1))

    missing_ids = found_ids - existing_ids
    added = 0
    if missing_ids:
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "a") as f:
            for vid_id in sorted(missing_ids):
                f.write(f"youtube {vid_id}\n")
                added += 1

    new_total = count_lines(archive_path)

    return jsonify({
        "status": "ok",
        "scanned_files": scanned,
        "ids_from_db": db_ids,
        "ids_on_disk": len(found_ids),
        "ids_in_archive_before": len(existing_ids),
        "ids_missing": len(missing_ids),
        "ids_added": added,
        "archive_total": new_total,
    })


# ── Library / Player Routes ──────────────────────────────────

@app.route("/library")
def library():
    from indexer import get_all_channels, get_library_stats, search_videos, get_index_status

    query = request.args.get("q", "").strip()
    index_status = get_index_status()

    if query:
        results = search_videos(query, limit=100)
        return render_template("library.html",
            page="library",
            channels=[],
            stats={},
            query=query,
            search_results=results,
            index_status=index_status,
            job=jobs,
            paused=is_paused(),
        )

    channels = get_all_channels()
    stats = get_library_stats()

    return render_template("library.html",
        page="library",
        channels=channels,
        stats=stats,
        query=None,
        index_status=index_status,
        job=jobs,
        paused=is_paused(),
    )


@app.route("/library/recent")
def library_recent():
    from indexer import get_recent_videos, get_index_status

    videos = get_recent_videos(limit=100)
    return render_template("recent.html",
        page="library",
        videos=videos,
        index_status=get_index_status(),
        job=jobs,
        paused=is_paused(),
    )


@app.route("/history")
def watch_history():
    from indexer import get_db

    filter_type = request.args.get("filter", "all")  # all, watched, in-progress

    conn = get_db()
    if filter_type == "watched":
        rows = conn.execute("""
            SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
            FROM watch_history wh
            JOIN videos v ON v.id = wh.video_id
            WHERE wh.watched = 1
            ORDER BY wh.last_watched DESC
            LIMIT 200
        """).fetchall()
    elif filter_type == "in-progress":
        rows = conn.execute("""
            SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
            FROM watch_history wh
            JOIN videos v ON v.id = wh.video_id
            WHERE wh.watched = 0 AND wh.position_seconds > 5
            ORDER BY wh.last_watched DESC
            LIMIT 200
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT v.*, wh.position_seconds, wh.watched, wh.last_watched
            FROM watch_history wh
            JOIN videos v ON v.id = wh.video_id
            ORDER BY wh.last_watched DESC
            LIMIT 200
        """).fetchall()
    conn.close()

    videos = [dict(r) for r in rows]

    # Stats
    conn2 = get_db()
    stats = {}
    r = conn2.execute("SELECT COUNT(*) as c FROM watch_history WHERE watched = 1").fetchone()
    stats['watched'] = r['c'] or 0
    r = conn2.execute("SELECT COUNT(*) as c FROM watch_history WHERE watched = 0 AND position_seconds > 5").fetchone()
    stats['in_progress'] = r['c'] or 0
    r = conn2.execute("SELECT COUNT(*) as c FROM watch_history").fetchone()
    stats['total'] = r['c'] or 0
    conn2.close()

    return render_template("history.html",
        page="history",
        videos=videos,
        filter_type=filter_type,
        stats=stats,
        job=jobs,
        paused=is_paused(),
    )


@app.route("/api/channel/<path:name>")
def api_channel_detail(name):
    """Return channel info + videos as JSON for the unified channels page."""
    from indexer import get_db, get_channel_playlists
    from urllib.parse import unquote

    name = unquote(name)
    conn = get_db()

    # Get channel info
    channel = conn.execute("SELECT * FROM channels WHERE name = ?", (name,)).fetchone()

    # Get videos with watch history
    videos = conn.execute("""
        SELECT v.id, v.title, v.upload_date, v.file_size, v.duration, v.file_path,
               v.thumbnail_path, v.view_count,
               wh.position_seconds, wh.watched
        FROM videos v
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        WHERE v.channel_name = ?
        ORDER BY v.upload_date DESC
    """, (name,)).fetchall()

    conn.close()

    # Get playlists
    playlists = get_channel_playlists(name)

    # Check for poster/banner existence
    channel_dir = Path(SHOWS_DIR) / name
    has_poster = (channel_dir / 'poster.jpg').exists() or (channel_dir / 'poster.png').exists() or (channel_dir / 'folder.jpg').exists()
    has_banner = (channel_dir / 'banner.jpg').exists() or (channel_dir / 'fanart.jpg').exists() or (channel_dir / 'banner.png').exists()

    # Check subscription status
    subscribed = any(ch['name'] == name for ch in get_channels())

    # Find channel URL
    channel_url = None
    for ch in get_channels():
        if ch['name'] == name:
            channel_url = ch['url']
            break

    video_list = []
    for v in videos:
        video_list.append({
            'id': v['id'],
            'title': v['title'],
            'date': v['upload_date'],
            'size': v['file_size'],
            'duration': v['duration'],
            'view_count': v['view_count'],
            'has_thumb': bool(v['thumbnail_path']),
            'watched': bool(v['watched']),
            'progress': v['position_seconds'] or 0,
        })

    return jsonify({
        'name': name,
        'video_count': len(videos),
        'total_size': sum(v['file_size'] or 0 for v in videos),
        'subscribed': subscribed,
        'has_poster': has_poster,
        'has_banner': has_banner,
        'on_disk': channel_dir.is_dir(),
        'channel_url': channel_url,
        'videos': video_list,
        'playlists': [{'id': p['id'], 'title': p['title'], 'video_count': p['video_count']} for p in playlists],
    })


@app.route("/library/<path:channel_name>")
def channel_view(channel_name):
    """Redirect old library channel URLs to unified channels page."""
    from urllib.parse import unquote, quote
    channel_name = unquote(channel_name)
    return redirect(f"/channels#{quote(channel_name)}")


@app.route("/library/<path:channel_name>/playlists")
def playlists_overview(channel_name):
    """Full page showing all playlists for a channel as a card grid."""
    from indexer import get_channel_playlists, get_index_status, get_db
    from urllib.parse import unquote

    channel_name = unquote(channel_name)
    playlists = get_channel_playlists(channel_name)

    # Get channel record for header
    conn = get_db()
    ch_row = conn.execute("SELECT * FROM channels WHERE name = ?", (channel_name,)).fetchone()
    channel_info = dict(ch_row) if ch_row else {}

    # For each playlist, grab the first video's thumbnail as the playlist thumbnail
    for pl in playlists:
        thumb_row = conn.execute("""
            SELECT v.id, v.thumbnail_path, v.title FROM video_playlists vp
            JOIN videos v ON v.id = vp.video_id
            WHERE vp.playlist_id = ? AND v.thumbnail_path IS NOT NULL
            ORDER BY vp.playlist_index ASC LIMIT 1
        """, (pl['id'],)).fetchone()
        pl['thumb_video_id'] = thumb_row['id'] if thumb_row else None
        pl['thumb_video_title'] = thumb_row['title'] if thumb_row else None

    conn.close()

    # Check if channel has playlist indexing enabled
    channel_has_playlist_flag = any(
        ch['name'] == channel_name and ch.get('index_playlists')
        for ch in get_channels()
    )
    channel_url = None
    for ch in get_channels():
        if ch['name'] == channel_name:
            channel_url = ch['url']
            break

    return render_template("playlists_overview.html",
        page="library",
        channel_name=channel_name,
        channel_info=channel_info,
        playlists=playlists,
        channel_has_playlist_flag=channel_has_playlist_flag,
        channel_url=channel_url,
        index_status=get_index_status(),
        job=jobs,
        paused=is_paused(),
    )


@app.route("/library/<path:channel_name>/playlist/<playlist_id>")
def playlist_view(channel_name, playlist_id):
    from indexer import get_playlist_videos, get_playlist, get_index_status, get_db, get_channel_playlists
    from urllib.parse import unquote

    channel_name = unquote(channel_name)
    sort = request.args.get("sort", "playlist_index")
    offset = request.args.get("offset", 0, type=int)
    limit = 60

    playlist = get_playlist(playlist_id)
    if not playlist:
        abort(404)

    videos, total = get_playlist_videos(playlist_id, sort=sort, limit=limit, offset=offset)

    # Get channel record for header
    conn = get_db()
    ch_row = conn.execute("SELECT * FROM channels WHERE name = ?", (channel_name,)).fetchone()
    channel_info = dict(ch_row) if ch_row else {}
    conn.close()

    # Get playlists for this channel
    playlists = get_channel_playlists(channel_name)

    # Check if channel has playlist indexing enabled
    channel_has_playlist_flag = any(
        ch['name'] == channel_name and ch.get('index_playlists')
        for ch in get_channels()
    )
    channel_url = None
    for ch in get_channels():
        if ch['name'] == channel_name:
            channel_url = ch['url']
            break

    return render_template("channel_view.html",
        page="library",
        channel_name=channel_name,
        channel_info=channel_info,
        latest_video=None,
        videos=videos,
        total=total,
        sort=sort,
        offset=offset,
        limit=limit,
        playlists=playlists,
        active_playlist=playlist,
        channel_has_playlist_flag=channel_has_playlist_flag,
        channel_url=channel_url,
        index_status=get_index_status(),
        job=jobs,
        paused=is_paused(),
    )


@app.route("/watch/<video_id>")
def watch(video_id):
    from indexer import get_video, get_channel_videos

    video = get_video(video_id)
    if not video:
        abort(404)

    # Get prev/next for navigation
    channel_videos, _ = get_channel_videos(video['channel_name'], sort='newest', limit=5000)
    prev_video = None
    next_video = None
    for i, v in enumerate(channel_videos):
        if v['id'] == video_id:
            if i > 0:
                prev_video = channel_videos[i - 1]
            if i < len(channel_videos) - 1:
                next_video = channel_videos[i + 1]
            break

    return render_template("watch.html",
        page="library",
        video=video,
        prev_video=prev_video,
        next_video=next_video,
        job=jobs,
        paused=is_paused(),
    )


@app.route("/delete/<video_id>", methods=["POST"])
def delete_video(video_id):
    """Delete a video file, its metadata, thumbnail, subtitles, and DB entry."""
    from indexer import get_video, get_db

    video = get_video(video_id)
    if not video:
        abort(404)

    channel_name = video.get('channel_name', '')

    # Delete the video file
    try:
        if video.get('file_path') and os.path.isfile(video['file_path']):
            os.remove(video['file_path'])
    except Exception as e:
        print(f"Error deleting video file: {e}")

    # Delete thumbnail
    try:
        if video.get('thumbnail_path') and os.path.isfile(video['thumbnail_path']):
            os.remove(video['thumbnail_path'])
    except Exception as e:
        print(f"Error deleting thumbnail: {e}")

    # Delete subtitle file
    try:
        if video.get('subtitle_path') and os.path.isfile(video['subtitle_path']):
            os.remove(video['subtitle_path'])
    except Exception as e:
        print(f"Error deleting subtitle: {e}")

    # Delete NFO file (same name, .nfo extension)
    try:
        if video.get('file_path'):
            nfo_path = os.path.splitext(video['file_path'])[0] + '.nfo'
            if os.path.isfile(nfo_path):
                os.remove(nfo_path)
    except Exception as e:
        print(f"Error deleting NFO: {e}")

    # Remove from database
    try:
        conn = get_db()
        conn.execute("DELETE FROM watch_history WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM video_playlists WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error removing from DB: {e}")

    # Remove from archive so it doesn't get re-downloaded
    # (keep it in archive — user intentionally deleted it)

    # Redirect back to channel
    if channel_name:
        return redirect(url_for("channel_view", channel_name=channel_name))
    return redirect(url_for("library"))


# ── Media Serving ──────────────────────────────────

@app.route("/media/video/<video_id>")
def serve_video(video_id):
    """Serve video file with range request support for seeking."""
    from indexer import get_video

    video = get_video(video_id)
    if not video or not os.path.isfile(video['file_path']):
        abort(404)

    file_path = video['file_path']
    file_size = os.path.getsize(file_path)
    mime = mimetypes.guess_type(file_path)[0] or 'video/mp4'

    # Handle range requests (required for video seeking)
    range_header = request.headers.get('Range')
    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(1024 * 1024, remaining)  # 1MB chunks
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            resp = Response(generate(), status=206, mimetype=mime)
            resp.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            resp.headers['Content-Length'] = length
            resp.headers['Accept-Ranges'] = 'bytes'
            return resp

    # Full file request
    return send_file(file_path, mimetype=mime)


@app.route("/media/thumb/<video_id>")
def serve_thumbnail(video_id):
    """Serve video thumbnail."""
    from indexer import get_video

    video = get_video(video_id)
    if not video or not video.get('thumbnail_path') or not os.path.isfile(video['thumbnail_path']):
        abort(404)

    return send_file(video['thumbnail_path'])


@app.route("/media/poster/<path:channel_name>")
def serve_poster(channel_name):
    """Serve channel poster/artwork."""
    from urllib.parse import unquote
    channel_name = unquote(channel_name)
    # Try common poster filenames
    for fname in ['poster.jpg', 'folder.jpg', 'poster.png']:
        path = os.path.join(SHOWS_DIR, channel_name, fname)
        if os.path.isfile(path):
            return send_file(path)
    abort(404)


@app.route("/media/banner/<path:channel_name>")
def serve_banner(channel_name):
    """Serve channel banner/fanart image."""
    from urllib.parse import unquote
    channel_name = unquote(channel_name)
    for fname in ['banner.jpg', 'fanart.jpg', 'banner.png']:
        path = os.path.join(SHOWS_DIR, channel_name, fname)
        if os.path.isfile(path):
            return send_file(path)
    abort(404)


@app.route("/media/subtitle/<video_id>")
def serve_subtitle(video_id):
    """Serve subtitle file, converting SRT to WebVTT if needed."""
    from indexer import get_video

    video = get_video(video_id)
    if not video or not video.get('subtitle_path') or not os.path.isfile(video['subtitle_path']):
        abort(404)

    sub_path = video['subtitle_path']

    if sub_path.endswith('.vtt'):
        return send_file(sub_path, mimetype='text/vtt')

    # Convert SRT to WebVTT on-the-fly
    try:
        with open(sub_path, 'r', errors='replace') as f:
            srt_content = f.read()

        vtt = "WEBVTT\n\n"
        # Replace SRT timestamps (comma) with VTT (dot)
        vtt += srt_content.replace(',', '.')

        return Response(vtt, mimetype='text/vtt')
    except:
        abort(500)


# ── API endpoints for player ──

@app.route("/api/watch-progress", methods=["POST"])
def api_watch_progress():
    """Save watch progress/status."""
    try:
        from indexer import update_watch_progress

        data = request.get_json(force=True, silent=True)
        if not data or 'video_id' not in data:
            return jsonify({"error": "missing video_id"}), 400

        update_watch_progress(
            video_id=data['video_id'],
            position_seconds=data.get('position', 0),
            watched=data.get('watched', False)
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/random-video/<path:channel_name>")
def api_random_video(channel_name):
    """Return a random video from a specific channel."""
    from indexer import get_channel_videos
    from urllib.parse import unquote
    import random
    channel_name = unquote(channel_name)
    videos, _ = get_channel_videos(channel_name, sort='newest', limit=5000)
    if not videos:
        return jsonify({"error": "No videos"}), 404
    exclude = request.args.get('exclude', '')
    skip_watched = request.args.get('skip_watched', '').lower() in ('1', 'true', 'yes')
    candidates = [v for v in videos if v['id'] != exclude]
    if skip_watched:
        unwatched = [v for v in candidates if not v.get('watched')]
        if unwatched:
            candidates = unwatched
    if not candidates:
        candidates = videos
    pick = random.choice(candidates)
    return jsonify({"id": pick['id'], "title": pick['title']})


@app.route("/api/random-video")
def api_random_video_any():
    """Return a random video from all channels or a subset."""
    from indexer import get_db
    import random
    channels_param = request.args.get('channels', '')
    exclude = request.args.get('exclude', '')
    skip_watched = request.args.get('skip_watched', '').lower() in ('1', 'true', 'yes')
    conn = get_db()
    if channels_param:
        channel_list = [c.strip() for c in channels_param.split(',')]
        placeholders = ','.join(['?' for _ in channel_list])
        rows = conn.execute(
            f"SELECT v.id, v.title, v.channel_name, wh.watched FROM videos v "
            f"LEFT JOIN watch_history wh ON v.id = wh.video_id "
            f"WHERE v.channel_name IN ({placeholders})", channel_list).fetchall()
    else:
        rows = conn.execute(
            "SELECT v.id, v.title, v.channel_name, wh.watched FROM videos v "
            "LEFT JOIN watch_history wh ON v.id = wh.video_id").fetchall()
    conn.close()
    candidates = [dict(r) for r in rows if r['id'] != exclude]
    if skip_watched:
        unwatched = [v for v in candidates if not v.get('watched')]
        if unwatched:
            candidates = unwatched
    if not candidates:
        return jsonify({"error": "No videos"}), 404
    pick = random.choice(candidates)
    return jsonify({"id": pick['id'], "title": pick['title'], "channel_name": pick['channel_name']})


@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    """Trigger a library re-index."""
    from indexer import get_index_status

    status = get_index_status()
    if status['scanning']:
        if request.args.get('force') or request.form.get('force'):
            return redirect(request.referrer or url_for("settings"))
        return jsonify({"status": "already scanning"})

    force = request.args.get('force', 'false').lower() == 'true'
    _trigger_background_index(force=force)

    # If called from browser (form submit), redirect back
    if request.referrer:
        return redirect(request.referrer)
    return jsonify({"status": "started"})


@app.route("/api/merge-hash-folders", methods=["POST"])
def api_merge_hash_folders():
    """One-click: merge all ChannelName# folders into ChannelName."""
    import shutil
    shows = Path(SHOWS_DIR)
    merged = 0
    errors = []
    for hdir in sorted(shows.iterdir()):
        if not hdir.is_dir() or not hdir.name.endswith('#'):
            continue
        if hdir.name.endswith('#playlists'):
            continue
        base_name = hdir.name.rstrip('#')
        base_path = hdir.parent / base_name
        try:
            if base_path.is_dir():
                for root, dirs, files in os.walk(str(hdir)):
                    rel_root = os.path.relpath(root, str(hdir))
                    dest_root = base_path / rel_root
                    for f in files:
                        src = Path(root) / f
                        dest = dest_root / f
                        if not dest.exists():
                            dest_root.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(src), str(dest))
                # Never delete unmoved (collided) files — remove empty dirs only
                for root, dirs, files in os.walk(str(hdir), topdown=False):
                    try:
                        os.rmdir(root)
                    except OSError:
                        pass
            else:
                hdir.rename(base_path)
            # DB cleanup
            try:
                from indexer import get_db as idx_get_db
                conn = idx_get_db()
                conn.execute("UPDATE videos SET channel_name = ? WHERE channel_name = ?", (base_name, hdir.name))
                # Fix path columns so thumbnails/playback survive the merge
                old_prefix = str(hdir) + "/"
                new_prefix = str(base_path) + "/"
                for col in ("file_path", "thumbnail_path", "subtitle_path", "json_path"):
                    conn.execute(
                        f"UPDATE videos SET {col} = ? || substr({col}, ?) "
                        f"WHERE {col} LIKE ? || '%'",
                        (new_prefix, len(old_prefix) + 1, old_prefix))
                conn.execute("UPDATE playlists SET channel_name = ? WHERE channel_name = ?", (base_name, hdir.name))
                conn.execute("UPDATE channels SET name = ?, path = ? WHERE name = ?",
                             (base_name, str(base_path), hdir.name))
                rows = conn.execute("SELECT COUNT(*) FROM channels WHERE name = ?", (base_name,)).fetchone()[0]
                if rows > 1:
                    conn.execute("DELETE FROM channels WHERE name = ? AND rowid NOT IN (SELECT MIN(rowid) FROM channels WHERE name = ?)", (base_name, base_name))
                conn.commit()
                conn.close()
            except Exception:
                pass
            merged += 1
        except Exception as e:
            errors.append(f"{hdir.name}: {e}")
    return jsonify({"merged": merged, "errors": errors})


@app.route("/api/cleanup-ghosts", methods=["POST"])
def api_cleanup_ghosts():
    """Remove database channel entries that don't match any entry in channels.txt.
    Only cleans DB records — never deletes video files from disk."""
    from indexer import get_db
    try:
        # Get all channel names from channels.txt
        valid_names = set()
        for ch in get_channels():
            valid_names.add(ch['name'])

        conn = get_db()
        db_channels = conn.execute("SELECT name FROM channels").fetchall()
        removed = []

        # Phase 0: rows whose media file no longer exists at file_path —
        # the leftovers of old # folder merges that only updated
        # channel_name (dead thumbnails / unplayable entries).
        # GUARD: if the shows dir is missing or empty (network share
        # unmounted), skip entirely — otherwise every row would look dead
        # and watch history would be lost with them. Only rows whose
        # channel folder exists are examined.
        dead_rows = 0
        shows_root = Path(SHOWS_DIR)
        if shows_root.is_dir() and any(shows_root.iterdir()):
            rows = conn.execute("SELECT id, file_path, channel_name FROM videos").fetchall()
            channel_exists = {}
            dead_ids = []
            for r in rows:
                fp = r['file_path'] or ''
                if not fp:
                    continue
                ch = r['channel_name']
                if ch not in channel_exists:
                    channel_exists[ch] = (shows_root / ch).is_dir()
                if not channel_exists[ch]:
                    continue  # handled by ghost-channel purge, not here
                if not os.path.isfile(fp):
                    dead_ids.append(r['id'])
            if dead_ids:
                ph = ",".join("?" * len(dead_ids))
                conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({ph})", dead_ids)
                conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({ph})", dead_ids)
                try:
                    conn.execute(f"""
                        INSERT INTO videos_fts(videos_fts, rowid, id, title, description, channel_name)
                        SELECT 'delete', rowid, id, title, description, channel_name
                        FROM videos WHERE id IN ({ph})
                    """, dead_ids)
                except Exception:
                    pass
                conn.execute(f"DELETE FROM videos WHERE id IN ({ph})", dead_ids)
                dead_rows = len(dead_ids)
                app.logger.info(f"Ghost cleanup: removed {dead_rows} video rows with missing files")

        for row in db_channels:
            name = row['name']
            if name not in valid_names:
                # Channel not in channels.txt — clean DB entries
                video_ids = [r['id'] for r in conn.execute(
                    "SELECT id FROM videos WHERE channel_name = ?", (name,)
                ).fetchall()]
                if video_ids:
                    placeholders = ",".join("?" * len(video_ids))
                    conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({placeholders})", video_ids)
                    conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({placeholders})", video_ids)
                    conn.execute(f"DELETE FROM videos WHERE channel_name = ?", (name,))
                try:
                    conn.execute("DELETE FROM videos_fts WHERE channel_name = ?", (name,))
                except Exception:
                    pass
                conn.execute("DELETE FROM playlists WHERE channel_name = ?", (name,))
                conn.execute("DELETE FROM channels WHERE name = ?", (name,))
                removed.append(name)

                # Remove empty folder (no video files) if it exists — leave folders with videos alone
                channel_dir = Path(SHOWS_DIR) / name
                if channel_dir.is_dir():
                    video_extensions = {'.mp4', '.mkv', '.webm', '.avi', '.mov'}
                    has_videos = any(
                        os.path.splitext(f)[1].lower() in video_extensions
                        for root, dirs, files in os.walk(str(channel_dir))
                        for f in files
                    )
                    if not has_videos:
                        import shutil
                        try:
                            shutil.rmtree(str(channel_dir))
                        except Exception:
                            pass

        conn.commit()

        # Also deduplicate: if multiple DB rows have the same channel name, keep only one
        dupes_removed = []
        all_names = conn.execute("SELECT name, COUNT(*) as cnt FROM channels GROUP BY name HAVING cnt > 1").fetchall()
        for row in all_names:
            name = row['name']
            # Keep the row with the highest rowid (most recent), delete older duplicates
            conn.execute("""
                DELETE FROM channels WHERE name = ? AND rowid NOT IN (
                    SELECT MAX(rowid) FROM channels WHERE name = ?
                )
            """, (name, name))
            dupes_removed.append(name)
        if dupes_removed:
            conn.commit()
            removed.extend([f"{n} (dedup)" for n in dupes_removed])

        parts = []
        if dead_rows:
            parts.append(f"{dead_rows} dead video row(s)")
        if removed:
            parts.append(f"{len(removed)} ghost channel(s): {', '.join(removed)}")
        msg = "Cleaned " + ", ".join(parts) if parts else "No ghosts found"
        return _ajax_or_redirect(msg, fallback="channels")

    except Exception as e:
        if _is_ajax():
            return jsonify({"status": "error", "message": str(e)}), 500
        return redirect(request.referrer or url_for("channels"))


@app.route("/api/index-status")
def api_index_status():
    from indexer import get_index_status
    return jsonify(get_index_status())


@app.route("/api/reindex/<path:channel_name>", methods=["POST"])
def api_reindex_channel(channel_name):
    """Re-index a single channel (synchronous — waits for result)."""
    from indexer import reindex_single_channel
    from urllib.parse import unquote
    import pathlib

    channel_name = unquote(channel_name)
    shows = pathlib.Path("/shows")
    channel_dir = shows / channel_name

    app.logger.info(f"Re-index requested for: {channel_name!r} → {channel_dir}")

    if not channel_dir.is_dir():
        app.logger.warning(f"Channel dir NOT FOUND: {channel_dir}")
        if _is_ajax():
            return jsonify({"status": "error", "message": f"Directory not found: {channel_name}"}), 404
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "error", "message": f"Directory not found: {channel_name}"}), 404

    # Run synchronously so the redirect shows updated counts
    result = reindex_single_channel(channel_name)

    return _ajax_or_redirect(f"Reindexed {channel_name}" if result else f"Error reindexing {channel_name}")


@app.route("/api/delete-folder/<path:channel_name>", methods=["POST"])
def api_delete_folder(channel_name):
    """Delete a channel's folder from disk and remove from database."""
    from urllib.parse import unquote
    import shutil

    channel_name = unquote(channel_name)
    channel_dir = Path(SHOWS_DIR) / channel_name

    if not channel_dir.is_dir():
        # No folder on disk — aggressively clean from database
        # Use case-insensitive matching and LIKE to catch any variant
        app.logger.info(f"No folder found for '{channel_name}', aggressive DB cleanup")
        try:
            conn = get_db()
            # Find all possible matching channel entries (case-insensitive, partial match)
            db_channels = conn.execute(
                "SELECT name FROM channels WHERE name = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE",
                (channel_name, f"%{channel_name}%")
            ).fetchall()
            names_to_delete = [r['name'] for r in db_channels]
            if channel_name not in names_to_delete:
                names_to_delete.append(channel_name)

            for dname in names_to_delete:
                video_ids = [r['id'] for r in conn.execute(
                    "SELECT id FROM videos WHERE channel_name = ?", (dname,)
                ).fetchall()]
                if video_ids:
                    placeholders = ",".join("?" * len(video_ids))
                    conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({placeholders})", video_ids)
                    conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({placeholders})", video_ids)
                    conn.execute(f"DELETE FROM videos WHERE channel_name = ?", (dname,))
                try:
                    conn.execute("DELETE FROM videos_fts WHERE channel_name = ?", (dname,))
                except Exception:
                    pass
                conn.execute("DELETE FROM playlists WHERE channel_name = ?", (dname,))
                conn.execute("DELETE FROM channels WHERE name = ?", (dname,))
                app.logger.info(f"Cleaned DB entries for: {dname}")
            conn.commit()
        except Exception as e:
            app.logger.error(f"DB cleanup error for {channel_name}: {e}")
        if _is_ajax():
            return jsonify({"status": "deleted", "channel": channel_name, "note": "database only, no folder found"})
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "deleted", "channel": channel_name, "note": "database only, no folder found"})

    try:
        shutil.rmtree(str(channel_dir))
        app.logger.info(f"Deleted folder: {channel_dir}")
    except Exception as e:
        app.logger.error(f"Failed to delete folder {channel_dir}: {e}")
        if _is_ajax():
            return jsonify({"status": "error", "message": str(e)}), 500
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "error", "message": str(e)}), 500

    # Also clean from database
    try:
        conn = get_db()
        video_ids = [r['id'] for r in conn.execute(
            "SELECT id FROM videos WHERE channel_name = ?", (channel_name,)
        ).fetchall()]
        if video_ids:
            placeholders = ",".join("?" * len(video_ids))
            conn.execute(f"DELETE FROM video_playlists WHERE video_id IN ({placeholders})", video_ids)
            conn.execute(f"DELETE FROM watch_history WHERE video_id IN ({placeholders})", video_ids)
            conn.execute(f"DELETE FROM videos_fts WHERE channel_name = ?", (channel_name,))
            conn.execute(f"DELETE FROM videos WHERE channel_name = ?", (channel_name,))
        conn.execute("DELETE FROM playlists WHERE channel_name = ?", (channel_name,))
        conn.execute("DELETE FROM channels WHERE name = ?", (channel_name,))
        conn.commit()
        app.logger.info(f"Cleaned database for: {channel_name}")
    except Exception as e:
        app.logger.error(f"DB cleanup error for {channel_name}: {e}")

    return _ajax_or_redirect(f"Deleted {channel_name}")


@app.route("/api/reindex-playlists/<path:channel_name>", methods=["POST"])
def api_reindex_playlists(channel_name):
    """Re-index playlists for a single channel (runs in background)."""
    from indexer import index_channel_playlists
    from urllib.parse import unquote

    channel_name = unquote(channel_name)

    # Find the channel URL from channels.txt
    channel_url = None
    for ch in get_channels():
        if ch['name'] == channel_name:
            channel_url = ch['url']
            break

    if not channel_url:
        if _is_ajax():
            return jsonify({"status": "error", "message": f"Channel not found in channels.txt: {channel_name}"}), 404
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "error", "message": f"Channel not found in channels.txt: {channel_name}"}), 404

    def _run_playlist_index():
        try:
            index_channel_playlists(channel_url, channel_name)
        except Exception as e:
            import traceback
            print(f"❌ Playlist reindex failed for {channel_name}: {e}")
            traceback.print_exc()

    thread = threading.Thread(target=_run_playlist_index, daemon=True)
    thread.start()

    return _ajax_or_redirect(f"Reindexing playlists for {channel_name}...")


@app.route("/api/fetch-art/<path:channel_name>", methods=["POST"])
def api_fetch_art(channel_name):
    """Fetch channel poster artwork from YouTube."""
    from urllib.parse import unquote
    import subprocess

    channel_name = unquote(channel_name)

    # Find the channel URL from channels.txt
    channel_url = None
    for ch in get_channels():
        if ch['name'] == channel_name:
            channel_url = ch['url']
            break

    if not channel_url:
        if _is_ajax():
            return jsonify({"status": "error", "message": f"Channel not found: {channel_name}"}), 404
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "error", "message": f"Channel not found: {channel_name}"}), 404

    channel_path = os.path.join(SHOWS_DIR, channel_name)
    cookies = "/config/Cookie/cookies.txt"

    def _fetch_art():
        import sqlite3 as _sqlite3
        from indexer import DB_PATH  # Single source of truth for DB location
        try:
            poster_path = os.path.join(channel_path, "poster")
            # Method 1: yt-dlp with --playlist-items 1
            result = subprocess.run([
                "yt-dlp",
                "--cookies", cookies,
                "--write-thumbnail",
                "--skip-download",
                "--playlist-items", "1",
                "--convert-thumbnails", "jpg",
                "-o", poster_path,
                channel_url
            ], capture_output=True, text=True, timeout=120)

            poster_file = os.path.join(channel_path, "poster.jpg")
            if os.path.isfile(poster_file):
                print(f"🎨 ✅ Fetched art for {channel_name}", flush=True)
                # Update DB directly (thread-safe, no Flask context needed)
                conn = _sqlite3.connect(DB_PATH, timeout=10)
                conn.execute("UPDATE channels SET has_poster = 1 WHERE name = ?", (channel_name,))
                conn.commit()
                conn.close()
            else:
                print(f"🎨 ⚠️ Could not fetch art for {channel_name}: {result.stderr[:200]}", flush=True)
        except Exception as e:
            import traceback
            print(f"🎨 ❌ Fetch art failed for {channel_name}: {e}", flush=True)
            traceback.print_exc()

    thread = threading.Thread(target=_fetch_art, daemon=True)
    thread.start()

    return _ajax_or_redirect(f"Fetching artwork for {channel_name}...")


@app.route("/api/toggle-playlists/<path:channel_name>", methods=["POST"])
def api_toggle_playlists(channel_name):
    """Toggle the |playlists flag for a channel in channels.txt."""
    from urllib.parse import unquote
    channel_name = unquote(channel_name)

    try:
        with open(CHANNELS_FILE, "r") as f:
            lines = f.readlines()

        new_lines = []
        toggled = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                new_lines.append(line)
                continue

            parts = stripped.split("|")
            url = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ""

            # Match by name
            if name == channel_name:
                has_playlists = any(p.strip().lower() == 'playlists' for p in parts[2:])
                if has_playlists:
                    # Remove the playlists flag
                    new_parts = [p for p in parts if p.strip().lower() != 'playlists']
                    new_lines.append("|".join(new_parts) + "\n")
                else:
                    # Add the playlists flag
                    new_lines.append(stripped + "|playlists\n")
                toggled = True
            else:
                new_lines.append(line)

        if toggled:
            with open(CHANNELS_FILE, "w") as f:
                f.writelines(new_lines)

    except Exception as e:
        print(f"Error toggling playlists for {channel_name}: {e}")

    return redirect(request.referrer or url_for("channels"))


# ── Bulk Video Operations ──

@app.route("/api/bulk-delete", methods=["POST"])
def api_bulk_delete():
    """Delete multiple videos from disk and add to archive (block re-download)."""
    from indexer import get_video, get_db
    data = request.get_json()
    if not data or not data.get("video_ids"):
        return jsonify({"status": "error", "error": "No video_ids provided"}), 400

    video_ids = data["video_ids"]
    deleted = 0
    excluded = 0

    for vid_id in video_ids:
        video = get_video(vid_id)
        if not video:
            continue

        # Delete files (video, thumbnail, subtitle, nfo)
        for key in ['file_path', 'thumbnail_path', 'subtitle_path']:
            try:
                path = video.get(key)
                if path and os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                app.logger.error(f"Error deleting {key} for {vid_id}: {e}")

        # Delete NFO
        try:
            if video.get('file_path'):
                nfo = os.path.splitext(video['file_path'])[0] + '.nfo'
                if os.path.isfile(nfo):
                    os.remove(nfo)
        except Exception:
            pass

        # Remove from database
        try:
            conn = get_db()
            conn.execute("DELETE FROM watch_history WHERE video_id = ?", (vid_id,))
            conn.execute("DELETE FROM video_playlists WHERE video_id = ?", (vid_id,))
            conn.execute("DELETE FROM videos WHERE id = ?", (vid_id,))
            conn.commit()
            conn.close()
            deleted += 1
        except Exception as e:
            app.logger.error(f"Error deleting {vid_id} from DB: {e}")

        # Add youtube ID to archive to block re-download (only if exclude requested)
        also_exclude = data.get('exclude', True)  # Default true for backward compat
        yt_id = video.get('youtube_id', vid_id)
        if yt_id and also_exclude:
            try:
                os.makedirs("/config/archive", exist_ok=True)
                with open(ARCHIVE_FILE, "a") as f:
                    f.write(f"youtube {yt_id}\n")
                excluded += 1
            except Exception as e:
                app.logger.error(f"Error adding {yt_id} to archive: {e}")

    return jsonify({"status": "ok", "deleted": deleted, "excluded": excluded})


@app.route("/api/bulk-exclude", methods=["POST"])
def api_bulk_exclude():
    """Add videos to archive/blocklist without deleting files."""
    from indexer import get_video
    data = request.get_json()
    if not data or not data.get("video_ids"):
        return jsonify({"status": "error", "error": "No video_ids provided"}), 400

    video_ids = data["video_ids"]
    excluded = 0

    os.makedirs("/config/archive", exist_ok=True)

    for vid_id in video_ids:
        video = get_video(vid_id)
        yt_id = video.get('youtube_id', vid_id) if video else vid_id

        if yt_id:
            try:
                with open(ARCHIVE_FILE, "a") as f:
                    f.write(f"youtube {yt_id}\n")
                excluded += 1
            except Exception as e:
                app.logger.error(f"Error excluding {yt_id}: {e}")

    return jsonify({"status": "ok", "excluded": excluded})


@app.route('/api/create-playlist', methods=['POST'])
def api_create_playlist():
    """Create a custom playlist from selected videos."""
    from indexer import get_db
    data = request.get_json()
    if not data or not data.get("video_ids") or not data.get("playlist_name"):
        return jsonify({"status": "error", "error": "video_ids and playlist_name required"}), 400

    video_ids = data["video_ids"]
    playlist_name = data["playlist_name"].strip()
    channel_name = data.get("channel_name", "Custom")

    # Generate a unique playlist ID from the name
    playlist_id = "custom_" + re.sub(r'[^a-zA-Z0-9]', '_', playlist_name.lower())

    try:
        conn = get_db()

        # Create or update the playlist entry
        conn.execute("""
            INSERT OR REPLACE INTO playlists (id, channel_name, title, video_count, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (playlist_id, channel_name, playlist_name, len(video_ids)))

        # Add videos to the playlist
        added = 0
        for idx, vid_id in enumerate(video_ids):
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO video_playlists (video_id, playlist_id, playlist_index)
                    VALUES (?, ?, ?)
                """, (vid_id, playlist_id, idx + 1))
                added += 1
            except Exception as e:
                app.logger.error(f"Error adding {vid_id} to playlist: {e}")

        # Update video count
        conn.execute("""
            UPDATE playlists SET video_count = (
                SELECT COUNT(*) FROM video_playlists WHERE playlist_id = ?
            ) WHERE id = ?
        """, (playlist_id, playlist_id))

        conn.commit()
        conn.close()

        return jsonify({"status": "ok", "added": added, "playlist_id": playlist_id})

    except Exception as e:
        app.logger.error(f"Error creating playlist: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Import Routes ──────────────────────────────────

def _normalize_channel_url(url):
    """Normalize a YouTube channel URL for comparison."""
    url = url.strip().rstrip('/')
    # Remove trailing /videos, /playlists, /streams etc.
    url = re.sub(r'/(videos|playlists|streams|shorts|about|community|channels|featured)$', '', url)
    return url.lower()


def _extract_channel_id(url):
    """Extract channel ID from URL if present."""
    m = re.search(r'/channel/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None


def _get_existing_channels():
    """Return set of normalized URLs, lowercase names, and channel IDs already in channels.txt."""
    channels = get_channels()
    urls = set()
    names = set()
    cids = set()
    for ch in channels:
        urls.add(_normalize_channel_url(ch['url']))
        names.add(ch['name'].lower().strip())
        cid = _extract_channel_id(ch['url'])
        if cid:
            cids.add(cid.lower())
    return urls, names, cids


def _check_already_exists(channel_url, channel_name, existing_urls, existing_names, existing_cids):
    """Check if a channel already exists by URL, name, or channel ID."""
    norm_url = _normalize_channel_url(channel_url)
    if norm_url in existing_urls:
        return True
    if channel_name and channel_name.lower().strip() in existing_names:
        return True
    cid = _extract_channel_id(channel_url)
    if cid and cid.lower() in existing_cids:
        return True
    return False


@app.route('/import/tubearchivist', methods=['POST'])
def import_tubearchivist():
    """Accept a TubeArchivist backup ZIP and parse channel data."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    try:
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        channels_found = []

        with zipfile.ZipFile(tmp_path, 'r') as zf:
            # Find channel data files
            channel_files = []
            for name in zf.namelist():
                basename = os.path.basename(name).lower()
                if (fnmatch.fnmatch(basename, '*channel*data*.json') or
                    fnmatch.fnmatch(basename, '*ta_channel*.json') or
                    fnmatch.fnmatch(basename, 'channel*.json')):
                    channel_files.append(name)

            if not channel_files:
                os.unlink(tmp_path)
                return jsonify({"error": "No channel data files found in ZIP. Expected files matching *channel*data*.json or *ta_channel*.json"}), 400

            for cf in channel_files:
                raw = zf.read(cf).decode('utf-8', errors='replace')
                parsed = []

                # Try nd-json first (one JSON object per line)
                lines = [l.strip() for l in raw.split('\n') if l.strip()]
                ndjson_ok = True
                for line in lines:
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            parsed.append(obj)
                        elif isinstance(obj, list):
                            # Could be a JSON array on a single line
                            parsed.extend(obj)
                            ndjson_ok = False
                            break
                    except json.JSONDecodeError:
                        ndjson_ok = False
                        break

                # If nd-json didn't work, try standard JSON array
                if not ndjson_ok or not parsed:
                    parsed = []
                    try:
                        data = json.loads(raw)
                        if isinstance(data, list):
                            parsed = data
                        elif isinstance(data, dict):
                            parsed = [data]
                    except json.JSONDecodeError:
                        continue  # Skip this file

                for entry in parsed:
                    if not isinstance(entry, dict):
                        continue

                    # Only include subscribed channels (if field exists)
                    if 'channel_subscribed' in entry and not entry['channel_subscribed']:
                        continue

                    channel_id = entry.get('channel_id', '')
                    channel_name = entry.get('channel_name', '')

                    if not channel_id and not channel_name:
                        continue

                    # Build URL
                    if channel_id:
                        url = f"https://www.youtube.com/channel/{channel_id}/videos"
                    else:
                        url = entry.get('channel_url', '')
                        if url and not url.endswith('/videos'):
                            url = url.rstrip('/') + '/videos'

                    if not url:
                        continue

                    channels_found.append({
                        "name": channel_name or channel_id,
                        "url": url,
                        "channel_id": channel_id,
                    })

        os.unlink(tmp_path)

        # Deduplicate by channel_id / URL
        seen = set()
        unique = []
        for ch in channels_found:
            key = ch.get('channel_id', '').lower() or _normalize_channel_url(ch['url'])
            if key not in seen:
                seen.add(key)
                unique.append(ch)

        # Check against existing
        existing_urls, existing_names, existing_cids = _get_existing_channels()
        for ch in unique:
            ch['already_exists'] = _check_already_exists(
                ch['url'], ch['name'], existing_urls, existing_names, existing_cids
            )

        new_count = sum(1 for ch in unique if not ch['already_exists'])
        existing_count = sum(1 for ch in unique if ch['already_exists'])

        return jsonify({
            "channels": unique,
            "total": len(unique),
            "new_count": new_count,
            "existing_count": existing_count,
        })

    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid ZIP file"}), 400
    except Exception as e:
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500


@app.route('/import/pinchflat', methods=['POST'])
def import_pinchflat():
    """Accept a PinchFlat SQLite database and parse channel sources."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    try:
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        channels_found = []
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row

        # Try different query patterns — PinchFlat schema may vary
        queries = [
            "SELECT original_url, custom_name FROM sources",
            "SELECT original_url, custom_name, collection_type FROM sources",
            "SELECT url AS original_url, name AS custom_name FROM sources",
            "SELECT url AS original_url, name AS custom_name FROM channels",
        ]

        rows = []
        for q in queries:
            try:
                rows = conn.execute(q).fetchall()
                if rows:
                    break
            except sqlite3.OperationalError:
                continue

        conn.close()
        os.unlink(tmp_path)

        if not rows:
            return jsonify({"error": "No channel sources found in database. Expected 'sources' or 'channels' table with URL data."}), 400

        for row in rows:
            url = row['original_url'] if row['original_url'] else ''
            name = row['custom_name'] if row['custom_name'] else ''

            if not url:
                continue

            # Normalize URL — ensure it ends with /videos if it's a channel URL
            url = url.strip().rstrip('/')
            if 'youtube.com' in url and '/videos' not in url and 'playlist' not in url:
                url += '/videos'

            if not name:
                # Try to extract name from URL
                if '/@' in url:
                    name = url.split('/@')[1].split('/')[0]
                elif '/channel/' in url:
                    name = url.split('/channel/')[1].split('/')[0][:20]
                else:
                    name = url

            channels_found.append({
                "name": name,
                "url": url,
            })

        # Deduplicate
        seen = set()
        unique = []
        for ch in channels_found:
            key = _normalize_channel_url(ch['url'])
            if key not in seen:
                seen.add(key)
                unique.append(ch)

        # Check against existing
        existing_urls, existing_names, existing_cids = _get_existing_channels()
        for ch in unique:
            ch['already_exists'] = _check_already_exists(
                ch['url'], ch['name'], existing_urls, existing_names, existing_cids
            )

        new_count = sum(1 for ch in unique if not ch['already_exists'])
        existing_count = sum(1 for ch in unique if ch['already_exists'])

        return jsonify({
            "channels": unique,
            "total": len(unique),
            "new_count": new_count,
            "existing_count": existing_count,
        })

    except Exception as e:
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500


@app.route('/import/scan-library', methods=['POST'])
def import_scan_library():
    """Scan an existing media directory for channel folders with .info.json metadata."""
    data = request.get_json()
    if not data or not data.get('path'):
        return jsonify({"error": "No directory path provided"}), 400

    scan_path = data['path'].strip()
    if not os.path.isdir(scan_path):
        return jsonify({"error": f"Directory not found: {scan_path}"}), 400

    channels_found = {}  # keyed by channel_id or folder name to deduplicate

    try:
        scan_root = Path(scan_path)
        for channel_dir in sorted(scan_root.iterdir()):
            if not channel_dir.is_dir():
                continue
            # Skip hidden dirs, metadata dirs
            if channel_dir.name.startswith('.') or channel_dir.name.startswith('_'):
                continue

            channel_id = None
            channel_url = None
            channel_name = None
            found_info = False

            # Search for first .info.json — check root of channel dir, then one level of subdirs (Season folders)
            search_dirs = [channel_dir]
            try:
                for sub in sorted(channel_dir.iterdir()):
                    if sub.is_dir() and not sub.name.startswith('.'):
                        search_dirs.append(sub)
            except PermissionError:
                pass

            for search_dir in search_dirs:
                if found_info:
                    break
                try:
                    for f in sorted(search_dir.iterdir()):
                        if f.is_file() and f.name.endswith('.info.json'):
                            try:
                                with open(f, 'r', errors='replace') as jf:
                                    info = json.load(jf)
                                channel_id = info.get('channel_id', '')
                                channel_url = info.get('channel_url', '')
                                channel_name = info.get('channel', '') or info.get('uploader', '') or info.get('uploader_id', '')
                                found_info = True
                                break  # Only read ONE .info.json per channel folder
                            except (json.JSONDecodeError, OSError):
                                continue
                except PermissionError:
                    continue

            # Build URL
            if channel_id and not channel_url:
                channel_url = f"https://www.youtube.com/channel/{channel_id}"

            if channel_url:
                channel_url = channel_url.strip().rstrip('/')
                if '/videos' not in channel_url and 'playlist' not in channel_url:
                    channel_url += '/videos'

            # Use folder name as fallback display name
            display_name = channel_name or channel_dir.name

            # Dedup key: prefer channel_id, fall back to folder name
            dedup_key = (channel_id or '').lower() or channel_dir.name.lower()

            if dedup_key not in channels_found:
                channels_found[dedup_key] = {
                    "name": display_name,
                    "url": channel_url or "",
                    "channel_id": channel_id or "",
                    "folder_name": channel_dir.name,
                    "has_metadata": found_info,
                }

    except PermissionError:
        return jsonify({"error": f"Permission denied reading: {scan_path}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error scanning directory: {str(e)}"}), 500

    unique = list(channels_found.values())

    if not unique:
        return jsonify({"error": f"No channel folders found in {scan_path}"}), 400

    # Check against existing
    existing_urls, existing_names, existing_cids = _get_existing_channels()
    for ch in unique:
        if ch['url']:
            ch['already_exists'] = _check_already_exists(
                ch['url'], ch['name'], existing_urls, existing_names, existing_cids
            )
        else:
            # No URL — match by name only
            ch['already_exists'] = ch['name'].lower().strip() in existing_names or ch['folder_name'].lower().strip() in existing_names

    new_count = sum(1 for ch in unique if not ch['already_exists'])
    existing_count = sum(1 for ch in unique if ch['already_exists'])

    return jsonify({
        "channels": unique,
        "total": len(unique),
        "new_count": new_count,
        "existing_count": existing_count,
    })


@app.route('/import/confirm', methods=['POST'])
def import_confirm():
    """Confirm import: append channels to channels.txt and create folders."""
    data = request.get_json()
    if not data or not data.get('channels'):
        return jsonify({"error": "No channels provided"}), 400

    source = data.get('source', 'import')
    channels_to_add = data['channels']

    # Re-check existing to avoid duplicates
    existing_urls, existing_names, existing_cids = _get_existing_channels()

    added = 0
    skipped = 0
    section_header = f"# --- Imported from {source.replace('_', ' ').title()} ---"

    lines_to_add = []
    for ch in channels_to_add:
        url = ch.get('url', '').strip()
        name = ch.get('name', '').strip()

        if not url:
            skipped += 1
            continue

        if _check_already_exists(url, name, existing_urls, existing_names, existing_cids):
            skipped += 1
            continue

        entry = f"{url}|{name}" if name else url
        lines_to_add.append(entry)

        # Create folder
        folder_name = name or url
        if folder_name:
            ch_dir = Path(SHOWS_DIR) / folder_name
            try:
                ch_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"Failed to create folder for {folder_name}: {e}")

        # Track as existing now to prevent duplicate adds within this batch
        existing_urls.add(_normalize_channel_url(url))
        if name:
            existing_names.add(name.lower().strip())
        cid = _extract_channel_id(url)
        if cid:
            existing_cids.add(cid.lower())

        added += 1

    if lines_to_add:
        with open(CHANNELS_FILE, "a") as f:
            f.write(f"\n{section_header}\n")
            for entry in lines_to_add:
                f.write(f"{entry}\n")

    return jsonify({
        "added": added,
        "skipped": skipped,
    })


# ── Health Check (per-channel, on-demand only) ──

HEALTHCHECK_DIR = "/config/healthcheck"
HEALTHCHECK_DB = os.path.join(HEALTHCHECK_DIR, "checked.json")
_healthcheck_state = {
    "status": "idle",  # idle, scanning, done, cancelled, error
    "channel": "",
    "current": 0,
    "total": 0,
    "healthy": 0,
    "broken": 0,
    "already_checked": 0,
    "total_in_channel": 0,
    "current_file": "",
    "mode": "full",
    "log": [],
}
_healthcheck_thread = None
_healthcheck_cancel = False


def _load_hc_db():
    """Load the persistent per-file health check database."""
    if not os.path.isfile(HEALTHCHECK_DB):
        return {}
    try:
        with open(HEALTHCHECK_DB) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_hc_db(db):
    """Save the persistent per-file health check database."""
    os.makedirs(HEALTHCHECK_DIR, exist_ok=True)
    with open(HEALTHCHECK_DB, "w") as f:
        json.dump(db, f)


def _run_healthcheck(channel, mode, throttle):
    """Background thread: scan a channel's video files with ffmpeg."""
    global _healthcheck_state, _healthcheck_cancel
    import time as _time

    shows_dir = "/shows"
    scan_dir = os.path.join(shows_dir, channel)

    state = _healthcheck_state
    state["log"] = []
    state["status"] = "scanning"
    state["channel"] = channel
    state["mode"] = mode
    state["current"] = 0
    state["healthy"] = 0
    state["broken"] = 0
    state["current_file"] = "Collecting files..."

    def log(msg):
        state["log"].append(msg)
        # Keep only last 200 lines
        if len(state["log"]) > 200:
            state["log"] = state["log"][-200:]

    if not os.path.isdir(scan_dir):
        state["status"] = "error"
        state["current_file"] = f"Channel folder not found: {scan_dir}"
        log(f"❌ {state['current_file']}")
        return

    # Find video files
    import glob
    extensions = ("*.mp4", "*.mkv", "*.webm", "*.avi")
    all_files = []
    for ext in extensions:
        all_files.extend(glob.glob(os.path.join(scan_dir, "**", ext), recursive=True))
    all_files.sort()
    state["total_in_channel"] = len(all_files)

    log(f"📊 Found {len(all_files)} video files in '{channel}'")

    # Filter already-checked files
    db = _load_hc_db()
    files_to_scan = []
    already_checked = 0

    for filepath in all_files:
        try:
            file_mtime = os.path.getmtime(filepath)
        except Exception:
            file_mtime = 0

        entry = db.get(filepath)
        if entry and entry.get("checked_mtime", 0) >= file_mtime:
            already_checked += 1
        else:
            files_to_scan.append(filepath)

    state["already_checked"] = already_checked
    state["total"] = len(files_to_scan)

    if len(files_to_scan) == 0:
        state["status"] = "done"
        state["current_file"] = "All files already checked!"
        log(f"✅ All {len(all_files)} videos already checked. Nothing to scan.")
        return

    log(f"🔍 {len(files_to_scan)} new files to scan ({already_checked} already checked)")
    log(f"   Mode: {'Quick (container only)' if mode == 'quick' else 'Full (decode every frame)'}")
    log("")

    import re
    id_pattern = re.compile(r'\[([a-zA-Z0-9_-]{11})\]\.[^.]+$')

    for idx, filepath in enumerate(files_to_scan, 1):
        if _healthcheck_cancel:
            state["status"] = "cancelled"
            state["current_file"] = "Cancelled — progress saved."
            log(f"\n🛑 Cancelled at {idx}/{len(files_to_scan)}")
            _healthcheck_cancel = False
            return

        filename = os.path.basename(filepath)
        rel_path = os.path.relpath(filepath, shows_dir)
        display = filename[:57] + "..." if len(filename) > 60 else filename

        state["current"] = idx
        state["current_file"] = display

        # Get file info
        try:
            file_size = os.path.getsize(filepath)
            file_mtime = os.path.getmtime(filepath)
        except Exception:
            file_size = 0
            file_mtime = 0

        size_mb = round(file_size / 1048576, 1)

        # Extract video ID
        video_id = ""
        m = id_pattern.search(filename)
        if m:
            video_id = m.group(1)

        # Run ffmpeg/ffprobe at low priority
        try:
            if mode == "quick":
                cmd = ["nice", "-n", "19", "ffprobe", "-v", "error", "-i", filepath]
            else:
                cmd = ["nice", "-n", "19", "ffmpeg", "-v", "error", "-i", filepath, "-f", "null", "-"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            error_output = result.stderr.strip()
        except subprocess.TimeoutExpired:
            error_output = "Timeout: file took too long to scan"
        except Exception as e:
            error_output = str(e)

        if not error_output:
            state["healthy"] += 1
            status = "healthy"
        else:
            state["broken"] += 1
            status = "broken"
            error_short = error_output[:200]
            log(f"❌ [{idx}/{len(files_to_scan)}] {rel_path} ({size_mb} MB)")
            log(f"   Error: {error_short}")

        # Save to persistent DB
        db[filepath] = {
            "status": status,
            "checked": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "checked_mtime": file_mtime,
            "size_mb": size_mb,
            "video_id": video_id,
            "channel": channel,
            "rel_path": rel_path,
            "error": error_output[:300] if status == "broken" else "",
        }

        # Save DB every 10 files (not every file — reduces disk writes)
        if idx % 10 == 0 or idx == len(files_to_scan):
            _save_hc_db(db)

        # Progress log every 25 files
        if idx % 25 == 0:
            log(f"📊 Progress: {idx}/{len(files_to_scan)} — {state['healthy']} healthy, {state['broken']} broken")

        # Throttle
        if throttle > 0 and idx < len(files_to_scan):
            _time.sleep(throttle)

    # Final save
    _save_hc_db(db)

    state["status"] = "done"
    state["current_file"] = f"Complete! {state['healthy']} healthy, {state['broken']} broken"
    log("")
    log("==========================================")
    log(f" 🩺 Health Check Complete: {channel}")
    log(f" ✅ Healthy:     {state['healthy']}")
    log(f" ❌ Broken:      {state['broken']}")
    log(f" ⏭️  Skipped:     {already_checked}")
    log("==========================================")


@app.route("/healthcheck")
def healthcheck_page():
    """Health check overview — shows per-channel status from persistent DB."""
    shows_dir = "/shows"
    channels = []
    if os.path.isdir(shows_dir):
        channels = sorted([
            d for d in os.listdir(shows_dir)
            if os.path.isdir(os.path.join(shows_dir, d)) and not d.startswith(".")
        ])

    # Load persistent DB to build per-channel stats
    channel_stats = {}
    db = _load_hc_db()
    for path, entry in db.items():
        ch = entry.get("channel", "")
        if ch not in channel_stats:
            channel_stats[ch] = {"healthy": 0, "broken": 0, "total": 0}
        channel_stats[ch]["total"] += 1
        if entry.get("status") == "healthy":
            channel_stats[ch]["healthy"] += 1
        else:
            channel_stats[ch]["broken"] += 1

    job = {"status": "idle"}
    paused = os.path.isfile("/config/.paused")
    return render_template("healthcheck.html", page="healthcheck", job=job, paused=paused,
                           channels=channels, channel_stats=channel_stats)


@app.route("/api/healthcheck/start", methods=["POST"])
def api_healthcheck_start():
    """Start a per-channel health check in a background thread."""
    global _healthcheck_thread, _healthcheck_cancel

    if _healthcheck_thread and _healthcheck_thread.is_alive():
        return jsonify({"error": "Health check already running"}), 409

    data = request.get_json() or {}
    channel = data.get("channel", "")
    mode = data.get("mode", "full")
    throttle = int(data.get("throttle", 1))

    if not channel:
        return jsonify({"error": "Channel name is required"}), 400

    _healthcheck_cancel = False
    _healthcheck_thread = threading.Thread(
        target=_run_healthcheck,
        args=(channel, mode, throttle),
        daemon=True
    )
    _healthcheck_thread.start()

    return jsonify({"status": "started", "channel": channel})


@app.route("/api/healthcheck/cancel", methods=["POST"])
def api_healthcheck_cancel():
    """Cancel a running health check."""
    global _healthcheck_cancel
    _healthcheck_cancel = True
    return jsonify({"status": "cancelling"})


@app.route("/api/healthcheck/progress")
def api_healthcheck_progress():
    """Get current scan progress from in-memory state."""
    s = _healthcheck_state
    total = s["total"] or 1
    pct = round(s["current"] / total * 100, 1) if s["total"] > 0 else 0

    return jsonify({
        "status": s["status"],
        "channel": s["channel"],
        "current": s["current"],
        "total": s["total"],
        "healthy": s["healthy"],
        "broken": s["broken"],
        "already_checked": s["already_checked"],
        "total_in_channel": s["total_in_channel"],
        "pct": pct,
        "current_file": s["current_file"],
        "mode": s["mode"],
    })


@app.route("/api/healthcheck/log")
def api_healthcheck_log():
    """Get health check log lines."""
    lines = int(request.args.get("lines", 100))
    log_lines = _healthcheck_state.get("log", [])
    tail = log_lines[-lines:] if len(log_lines) > lines else log_lines
    return jsonify({"log": "\n".join(tail), "total_lines": len(log_lines)})


@app.route("/api/healthcheck/channel/<path:channel_name>")
def api_healthcheck_channel(channel_name):
    """Get health check results for a specific channel."""
    db = _load_hc_db()

    files = []
    healthy = 0
    broken = 0
    for path, entry in db.items():
        if entry.get("channel") == channel_name:
            files.append(entry)
            if entry.get("status") == "healthy":
                healthy += 1
            else:
                broken += 1

    files.sort(key=lambda x: (0 if x.get("status") == "broken" else 1, x.get("rel_path", "")))

    return jsonify({
        "channel": channel_name,
        "checked": len(files),
        "healthy": healthy,
        "broken": broken,
        "files": files
    })


@app.route("/api/healthcheck/fix", methods=["POST"])
def api_healthcheck_fix():
    """Remove broken videos from archive so they get re-downloaded."""
    data = request.get_json() or {}
    single_id = data.get("video_id")
    channel = data.get("channel")

    db = _load_hc_db()

    archive_file = "/config/archive/downloaded.txt"
    queued = 0
    paths_to_remove = []

    for path, entry in db.items():
        if entry.get("status") != "broken":
            continue
        if channel and entry.get("channel") != channel:
            continue
        vid = entry.get("video_id")
        if not vid:
            continue
        if single_id and vid != single_id:
            continue

        if os.path.isfile(archive_file):
            try:
                with open(archive_file, "r") as af:
                    lines = af.readlines()
                with open(archive_file, "w") as af:
                    for line in lines:
                        if vid not in line:
                            af.write(line)
            except Exception:
                pass

        if os.path.isfile(path):
            try:
                os.remove(path)
            except Exception:
                pass

        paths_to_remove.append(path)
        queued += 1

    for p in paths_to_remove:
        db.pop(p, None)
    _save_hc_db(db)

    return jsonify({"status": "queued", "queued": queued})


# ── Temp file scanner state (in-memory, like health check) ──
_temp_scan_state = {
    "status": "idle",          # idle | scanning | done
    "dirs_scanned": 0,
    "files_found": 0,
    "current_dir": "",
    "results": [],
}

@app.route("/api/healthcheck/temp-scan", methods=["POST"])
def api_temp_scan():
    """Start scanning library for .temp / .part files (background thread)."""
    if _temp_scan_state["status"] == "scanning":
        return jsonify({"status": "already_running"})

    _temp_scan_state["status"] = "scanning"
    _temp_scan_state["dirs_scanned"] = 0
    _temp_scan_state["files_found"] = 0
    _temp_scan_state["current_dir"] = ""
    _temp_scan_state["results"] = []

    def scan_worker():
        shows_dir = os.environ.get("SHOWS_DIR", "/shows")
        temp_extensions = {'.temp', '.part', '.ytdl'}
        found = []

        for root, dirs, files in os.walk(shows_dir):
            _temp_scan_state["dirs_scanned"] += 1
            _temp_scan_state["current_dir"] = os.path.basename(root)
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in temp_extensions:
                    path = os.path.join(root, f)
                    vid_match = re.search(r'\[([a-zA-Z0-9_-]{11})\]', f)
                    video_id = vid_match.group(1) if vid_match else None
                    channel = os.path.basename(root)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    found.append({
                        "path": path,
                        "filename": f,
                        "channel": channel,
                        "video_id": video_id,
                        "size": size,
                        "ext": ext,
                    })
                    _temp_scan_state["files_found"] = len(found)

        _temp_scan_state["results"] = found
        _temp_scan_state["status"] = "done"

    t = threading.Thread(target=scan_worker, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/healthcheck/temp-progress")
def api_temp_progress():
    """Poll scan progress."""
    return jsonify({
        "status": _temp_scan_state["status"],
        "dirs_scanned": _temp_scan_state["dirs_scanned"],
        "files_found": _temp_scan_state["files_found"],
        "current_dir": _temp_scan_state["current_dir"],
        "results": _temp_scan_state["results"] if _temp_scan_state["status"] == "done" else [],
        "count": len(_temp_scan_state["results"]) if _temp_scan_state["status"] == "done" else _temp_scan_state["files_found"],
    })


@app.route("/api/healthcheck/temp-fix", methods=["POST"])
def api_temp_fix():
    """Delete .temp/.part files and remove their IDs from archive so yt-dlp re-downloads."""
    data = request.get_json() or {}
    fix_all = data.get("all", False)
    single_path = data.get("path")

    archive_file = "/config/archive/downloaded.txt"
    shows_dir = os.environ.get("SHOWS_DIR", "/shows")
    temp_extensions = {'.temp', '.part', '.ytdl'}
    fixed = 0

    # Collect files to fix
    targets = []
    if single_path and os.path.isfile(single_path):
        targets.append(single_path)
    elif fix_all:
        for root, dirs, files in os.walk(shows_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in temp_extensions:
                    targets.append(os.path.join(root, f))

    for path in targets:
        fname = os.path.basename(path)
        vid_match = re.search(r'\[([a-zA-Z0-9_-]{11})\]', fname)
        video_id = vid_match.group(1) if vid_match else None

        # Remove from archive so yt-dlp will re-download
        if video_id and os.path.isfile(archive_file):
            try:
                with open(archive_file, "r") as af:
                    lines = af.readlines()
                with open(archive_file, "w") as af:
                    for line in lines:
                        if video_id not in line:
                            af.write(line)
            except Exception:
                pass

        # Delete the incomplete file
        try:
            os.remove(path)
            fixed += 1
        except Exception:
            pass

    return jsonify({"status": "ok", "fixed": fixed})


# ── Startup ──

BACKUP_DIR = "/config/backups"

def _make_backup():
    import zipfile, glob as _g
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"slugtube-config-{stamp}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _g.glob("/config/*"):
            if os.path.isfile(f) and not f.endswith((".zip", ".log")):
                z.write(f, os.path.basename(f))
    backups = sorted(_g.glob(os.path.join(BACKUP_DIR, "*.zip")))
    for old in backups[:-14]:
        try: os.remove(old)
        except OSError: pass
    return path

@app.route("/api/backup-now", methods=["POST"])
def api_backup_now():
    try:
        return jsonify({"status": "ok", "path": _make_backup()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

def _daily_backup_loop():
    import time as _t
    while True:
        try: _make_backup()
        except Exception: pass
        _t.sleep(86400)

threading.Thread(target=_daily_backup_loop, daemon=True).start()

if __name__ == "__main__":
    os.makedirs("/config/logs", exist_ok=True)
    os.makedirs("/config/archive", exist_ok=True)
    os.makedirs("/config/Cookie", exist_ok=True)

    # Init config if missing
    if not os.path.isfile(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)

    # Start background indexer on boot
    try:
        from indexer import init_db
        init_db()
        _trigger_background_index()
        print("📚 Background indexer started")
    except Exception as e:
        print(f"⚠️ Indexer init failed: {e}")

    app.run(host="0.0.0.0", port=5000, debug=False)


# ============================================================
# Thumbnail Rebuild — relink sidecar images, fetch missing ones
# ============================================================

_thumb_job = {"running": False, "phase": "", "checked": 0, "relinked": 0,
              "fetched": 0, "failed": 0, "skipped_synthetic": 0, "done_at": ""}

_THUMB_SIDECAR_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


def _find_sidecar_thumb(video_path: str) -> str:
    """Look next to the video file for <base>.jpg / <base>-thumb.jpg etc."""
    base, _ = os.path.splitext(video_path)
    for suffix in ('', '-thumb'):
        for ext in _THUMB_SIDECAR_EXTS:
            candidate = base + suffix + ext
            if os.path.isfile(candidate):
                return candidate
    return ''


def _fetch_youtube_thumb(video_id: str, dest_path: str) -> bool:
    """Download the YouTube thumbnail for a real video id. Never overwrites."""
    import urllib.request
    if os.path.exists(dest_path):
        return False
    for variant in ("maxresdefault", "hqdefault", "mqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{variant}.jpg"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) < 2000:  # placeholder gray thumbs are tiny
                continue
            tmp = dest_path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest_path)
            return True
        except Exception:
            continue
    return False


def _rebuild_thumbnails_worker(fetch_missing: bool):
    from indexer import get_db as idx_get_db
    import time as _time
    try:
        conn = idx_get_db()
        rows = conn.execute(
            "SELECT id, file_path, thumbnail_path FROM videos").fetchall()

        # Phase 1: relink — thumbnail missing/dead in DB but a sidecar
        # image already exists next to the video file
        _thumb_job["phase"] = "relinking"
        for r in rows:
            _thumb_job["checked"] += 1
            fp = r["file_path"] or ""
            tp = r["thumbnail_path"] or ""
            if not fp or not os.path.isfile(fp):
                continue  # dead rows are Clean Ghost Entries' job
            if tp and os.path.isfile(tp):
                continue  # already fine
            found = _find_sidecar_thumb(fp)
            if found:
                conn.execute("UPDATE videos SET thumbnail_path = ? WHERE id = ?",
                             (found, r["id"]))
                _thumb_job["relinked"] += 1
        conn.commit()

        # Phase 2: fetch from YouTube for real-ID videos still missing one.
        # Writes <videobase>-thumb.jpg ONLY when no such file exists —
        # never overwrites anything.
        if fetch_missing:
            _thumb_job["phase"] = "fetching"
            still_missing = conn.execute("""
                SELECT id, file_path FROM videos
                WHERE (thumbnail_path IS NULL OR thumbnail_path = '')
                  AND file_path != ''
            """).fetchall()
            for r in still_missing:
                vid = r["id"]
                fp = r["file_path"] or ""
                if not fp or not os.path.isfile(fp):
                    continue
                if vid.startswith("f_") or len(vid) != 11:
                    _thumb_job["skipped_synthetic"] += 1
                    continue
                dest = os.path.splitext(fp)[0] + "-thumb.jpg"
                if _fetch_youtube_thumb(vid, dest):
                    conn.execute(
                        "UPDATE videos SET thumbnail_path = ? WHERE id = ?",
                        (dest, vid))
                    _thumb_job["fetched"] += 1
                    _time.sleep(0.05)  # be polite to i.ytimg.com
                else:
                    _thumb_job["failed"] += 1
            conn.commit()
        conn.close()
    except Exception as e:
        app.logger.error(f"Thumbnail rebuild error: {e}")
    finally:
        _thumb_job["running"] = False
        _thumb_job["phase"] = "done"
        _thumb_job["done_at"] = datetime.now().strftime("%H:%M:%S")
        app.logger.info(
            f"Thumbnail rebuild: {_thumb_job['relinked']} relinked, "
            f"{_thumb_job['fetched']} fetched, {_thumb_job['failed']} failed, "
            f"{_thumb_job['skipped_synthetic']} skipped (no YouTube id) "
            f"of {_thumb_job['checked']} checked")


@app.route("/api/rebuild-thumbnails", methods=["POST"])
def api_rebuild_thumbnails():
    """Relink sidecar thumbnails; optionally fetch missing ones from YouTube.

    Non-destructive: only updates DB paths and creates new -thumb.jpg files
    where none exist. Never deletes or overwrites any file."""
    if _thumb_job["running"]:
        return _ajax_or_redirect("Thumbnail rebuild already running", fallback="settings")
    fetch_missing = request.args.get("fetch", "1") != "0"
    for k in ("checked", "relinked", "fetched", "failed", "skipped_synthetic"):
        _thumb_job[k] = 0
    _thumb_job.update({"running": True, "phase": "starting", "done_at": ""})
    threading.Thread(target=_rebuild_thumbnails_worker,
                     args=(fetch_missing,), daemon=True).start()
    return _ajax_or_redirect(
        "Thumbnail rebuild started — relinking sidecar images, then fetching "
        "missing thumbs from YouTube. Check /api/rebuild-thumbnails/status.",
        fallback="settings")


@app.route("/api/rebuild-thumbnails/status")
def api_rebuild_thumbnails_status():
    return jsonify(_thumb_job)


# ============================================================
# Personal Playlists — user-curated, independent of channels
# (channel_name 'Custom' — compatible with /api/create-playlist)
# ============================================================

PERSONAL_CHANNEL = "Custom"


def _personal_playlist_rows(conn, video_id=None):
    rows = conn.execute("""
        SELECT p.id, p.title, p.video_count,
               (SELECT vp.video_id FROM video_playlists vp
                WHERE vp.playlist_id = p.id ORDER BY vp.playlist_index LIMIT 1) AS thumb_video_id
        FROM playlists p WHERE p.channel_name = ? ORDER BY p.title
    """, (PERSONAL_CHANNEL,)).fetchall()
    result = []
    member_of = set()
    if video_id:
        member_of = {r["playlist_id"] for r in conn.execute(
            "SELECT playlist_id FROM video_playlists WHERE video_id = ?", (video_id,))}
    for r in rows:
        d = dict(r)
        if video_id:
            d["has_video"] = r["id"] in member_of
        result.append(d)
    return result


def _refresh_playlist_count(conn, pid):
    conn.execute("""UPDATE playlists SET video_count =
        (SELECT COUNT(*) FROM video_playlists WHERE playlist_id = ?),
        updated_at = datetime('now') WHERE id = ?""", (pid, pid))


@app.route("/playlists")
def my_playlists_page():
    from indexer import get_db
    conn = get_db()
    playlists = _personal_playlist_rows(conn)
    conn.close()
    return render_template("my_playlists.html", playlists=playlists,
                           detail=None, videos=[], page="playlists", job=jobs)


@app.route("/playlists/<playlist_id>")
def my_playlist_detail(playlist_id):
    from indexer import get_db, get_playlist, get_playlist_videos
    pl = get_playlist(playlist_id)
    if not pl or pl.get("channel_name") != PERSONAL_CHANNEL:
        abort(404)
    videos, total = get_playlist_videos(playlist_id, sort="playlist_index",
                                        limit=500, offset=0)
    conn = get_db()
    playlists = _personal_playlist_rows(conn)
    conn.close()
    return render_template("my_playlists.html", playlists=playlists,
                           detail=pl, videos=videos, page="playlists", job=jobs)


@app.route("/api/personal-playlists", methods=["GET", "POST"])
def api_personal_playlists():
    from indexer import get_db
    conn = get_db()
    if request.method == "GET":
        video_id = request.args.get("video_id")
        pls = _personal_playlist_rows(conn, video_id=video_id)
        conn.close()
        return jsonify({"playlists": pls})

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        conn.close()
        return jsonify({"status": "error", "error": "name required"}), 400
    pid = "custom_" + re.sub(r'[^a-zA-Z0-9]', '_', name.lower())
    conn.execute("""INSERT OR IGNORE INTO playlists
                    (id, channel_name, title, video_count, updated_at)
                    VALUES (?, ?, ?, 0, datetime('now'))""",
                 (pid, PERSONAL_CHANNEL, name))
    conn.commit()
    pls = _personal_playlist_rows(conn)
    conn.close()
    return jsonify({"status": "ok", "id": pid, "playlists": pls})


@app.route("/api/personal-playlists/<playlist_id>/videos", methods=["POST"])
def api_personal_playlist_add(playlist_id):
    from indexer import get_db
    data = request.get_json(silent=True) or {}
    video_id = data.get("video_id")
    if not video_id:
        return jsonify({"status": "error", "error": "video_id required"}), 400
    conn = get_db()
    pl = conn.execute("SELECT id FROM playlists WHERE id = ? AND channel_name = ?",
                      (playlist_id, PERSONAL_CHANNEL)).fetchone()
    if not pl:
        conn.close()
        return jsonify({"status": "error", "error": "playlist not found"}), 404
    nxt = conn.execute(
        "SELECT COALESCE(MAX(playlist_index), 0) + 1 AS n FROM video_playlists WHERE playlist_id = ?",
        (playlist_id,)).fetchone()["n"]
    conn.execute("""INSERT OR IGNORE INTO video_playlists
                    (video_id, playlist_id, playlist_index) VALUES (?, ?, ?)""",
                 (video_id, playlist_id, nxt))
    _refresh_playlist_count(conn, playlist_id)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/personal-playlists/<playlist_id>/videos/<video_id>", methods=["DELETE"])
def api_personal_playlist_remove(playlist_id, video_id):
    from indexer import get_db
    conn = get_db()
    conn.execute("DELETE FROM video_playlists WHERE playlist_id = ? AND video_id = ?",
                 (playlist_id, video_id))
    _refresh_playlist_count(conn, playlist_id)
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/personal-playlists/<playlist_id>", methods=["DELETE"])
def api_personal_playlist_delete(playlist_id):
    """Deletes the playlist record only — never any video files."""
    from indexer import get_db
    conn = get_db()
    pl = conn.execute("SELECT id FROM playlists WHERE id = ? AND channel_name = ?",
                      (playlist_id, PERSONAL_CHANNEL)).fetchone()
    if not pl:
        conn.close()
        return jsonify({"status": "error", "error": "playlist not found"}), 404
    conn.execute("DELETE FROM video_playlists WHERE playlist_id = ?", (playlist_id,))
    conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})
