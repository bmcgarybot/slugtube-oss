from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, abort, Response
import subprocess
import os
import threading
import json
import mimetypes
import re
import time
from datetime import datetime
from pathlib import Path

app = Flask(__name__)

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


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"last_fast": None, "last_full": None, "total_downloaded": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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


def get_recent_downloads(count=20):
    downloads = []
    try:
        result = subprocess.run(
            ["grep", "-E", "(Downloading|Already|\\[download\\]|✅|⏭️|❌)", LOG_FILE],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            lines = result.stdout.strip().split("\n")[-count:]
            for line in lines:
                downloads.append(line.strip())
    except:
        pass
    return downloads


def get_ytdlp_version():
    try:
        ver = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5)
        return ver.stdout.strip()
    except:
        return "unknown"


def check_cookie_health():
    """Check cookie file health: expiration, age, existence."""
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

        if file_age_days > 7:
            return {"status": "warning", "message": f"Cookie file is {round(file_age_days)} days old", "age_days": round(file_age_days, 1), "expired_count": expired, "total_count": total}

        return {"status": "ok", "message": f"Cookies OK ({round(file_age_days, 1)} days old)", "age_days": round(file_age_days, 1), "expired_count": expired, "total_count": total}

    except Exception as e:
        return {"status": "missing", "message": f"Error reading cookies: {e}", "age_days": 0, "expired_count": 0, "total_count": 0}


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
        jobs["status"] = "done" if proc.returncode == 0 else "error"

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

        jobs["status"] = "done" if proc.returncode == 0 else "error"
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
    except Exception as e:
        jobs["status"] = "error"
        jobs["log"] = str(e)


# ── Original Routes ──────────────────────────────────────────────

@app.route("/")
def dashboard():
    channels = get_channels()
    archive_count = count_lines(ARCHIVE_FILE)
    disk_usage = get_disk_usage()
    state = load_state()
    config = load_config()
    cookie_health = check_cookie_health()

    # Use the indexer database for stats (same source as Library page)
    from indexer import get_library_stats, get_all_channels, get_index_status
    idx_stats = get_library_stats()
    idx_channels = get_all_channels()
    idx_status = get_index_status()
    total_videos = idx_stats.get('total_videos', 0)
    top_channels = sorted(idx_channels, key=lambda c: c.get('video_count', 0), reverse=True)[:10]

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
    fast_cron = os.environ.get("FAST_CRON", "0 */6 * * *")
    full_cron = os.environ.get("FULL_CRON", "0 3 * * 0")
    update_cron = os.environ.get("UPDATE_CRON", "0 1 * * *")
    state = load_state()
    config = load_config()
    save_status = request.args.get("saved", None)
    return render_template("settings.html",
        page="settings",
        fast_cron=fast_cron,
        full_cron=full_cron,
        update_cron=update_cron,
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

    return redirect(request.referrer or url_for("channels"))


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
        return redirect(request.referrer or url_for("channels"))
    if is_paused():
        set_paused(False)
    thread = threading.Thread(target=run_single_channel, args=(url, folder_name), daemon=True)
    thread.start()
    return redirect(request.referrer or url_for("channels"))


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
        return redirect(request.referrer or url_for("channels"))
    if is_paused():
        set_paused(False)
    thread = threading.Thread(target=run_single_channel, args=(url, folder_name, True), daemon=True)
    thread.start()
    return redirect(request.referrer or url_for("channels"))


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
    return jsonify(check_cookie_health())


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


@app.route("/library/<path:channel_name>")
def channel_view(channel_name):
    from indexer import get_channel_videos, get_index_status, get_db, get_channel_playlists
    from urllib.parse import unquote

    channel_name = unquote(channel_name)
    sort = request.args.get("sort", "newest")
    offset = request.args.get("offset", 0, type=int)
    limit = 60

    videos, total = get_channel_videos(channel_name, sort=sort, limit=limit, offset=offset)

    # Get channel record for header
    conn = get_db()
    ch_row = conn.execute("SELECT * FROM channels WHERE name = ?", (channel_name,)).fetchone()
    channel_info = dict(ch_row) if ch_row else {}

    # Get the latest video for the featured spot
    latest_row = conn.execute("""
        SELECT v.*, wh.position_seconds, wh.watched
        FROM videos v
        LEFT JOIN watch_history wh ON v.id = wh.video_id
        WHERE v.channel_name = ?
        ORDER BY v.upload_date DESC
        LIMIT 1
    """, (channel_name,)).fetchone()
    latest_video = dict(latest_row) if latest_row else None
    conn.close()

    # Get playlists for this channel
    playlists = get_channel_playlists(channel_name)

    # Check if channel has playlist indexing enabled in channels.txt
    channel_has_playlist_flag = any(
        ch['name'] == channel_name and ch.get('index_playlists')
        for ch in get_channels()
    )
    # Also find the channel URL for the reindex button
    channel_url = None
    for ch in get_channels():
        if ch['name'] == channel_name:
            channel_url = ch['url']
            break

    return render_template("channel_view.html",
        page="library",
        channel_name=channel_name,
        channel_info=channel_info,
        latest_video=latest_video,
        videos=videos,
        total=total,
        sort=sort,
        offset=offset,
        limit=limit,
        playlists=playlists,
        active_playlist=None,
        channel_has_playlist_flag=channel_has_playlist_flag,
        channel_url=channel_url,
        index_status=get_index_status(),
        job=jobs,
        paused=is_paused(),
    )


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
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "error", "message": f"Directory not found: {channel_name}"}), 404

    # Run synchronously so the redirect shows updated counts
    result = reindex_single_channel(channel_name)

    if request.referrer:
        return redirect(request.referrer)
    return jsonify({"status": "done" if result else "error", "channel": channel_name})


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
        if request.referrer:
            return redirect(request.referrer)
        return jsonify({"status": "deleted", "channel": channel_name, "note": "database only, no folder found"})

    try:
        shutil.rmtree(str(channel_dir))
        app.logger.info(f"Deleted folder: {channel_dir}")
    except Exception as e:
        app.logger.error(f"Failed to delete folder {channel_dir}: {e}")
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

    if request.referrer:
        return redirect(request.referrer)
    return jsonify({"status": "deleted", "channel": channel_name})


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

    if request.referrer:
        return redirect(request.referrer)
    return jsonify({"status": "started", "channel": channel_name})


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

    if request.referrer:
        return redirect(request.referrer)
    return jsonify({"status": "started", "channel": channel_name})


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

        # Add youtube ID to archive to block re-download
        yt_id = video.get('youtube_id', vid_id)
        if yt_id:
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


# ── Startup ──

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
