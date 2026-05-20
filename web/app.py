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
jobs = {"status": "idle", "mode": None, "started": None, "log": ""}

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
                    url = line.strip()
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
                    channels.append({"url": url, "name": name, "section": current_section, "type": "channel"})
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


def get_log_tail(lines=100):
    try:
        result = subprocess.run(["tail", "-n", str(lines), LOG_FILE], capture_output=True, text=True, timeout=5)
        return result.stdout
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


def run_single_channel(url):
    """Download a single channel (full, no playlist-end limit)."""
    if is_paused():
        jobs["status"] = "paused"
        jobs["log"] = "Downloads are paused. Resume from the dashboard."
        return

    jobs["status"] = "running"
    jobs["mode"] = "single"
    jobs["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        result = subprocess.run(
            ["/app/scripts/download.sh", "--single", url],
            capture_output=True, text=True, timeout=14400
        )
        jobs["log"] = result.stdout[-3000:] if result.stdout else "No output"
        jobs["status"] = "done"
        # Trigger re-index after downloads complete
        try:
            from indexer import start_background_index
            start_background_index()
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
        result = subprocess.run(
            ["/app/scripts/download.sh", f"--{mode}"],
            capture_output=True, text=True, timeout=14400
        )
        jobs["log"] = result.stdout[-3000:] if result.stdout else "No output"
        jobs["status"] = "done"
        state = load_state()
        state[f"last_{mode}"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        # Trigger re-index after downloads complete
        try:
            from indexer import start_background_index
            start_background_index()
        except:
            pass
    except subprocess.TimeoutExpired:
        jobs["status"] = "timeout"
        jobs["log"] = "Job timed out after 4 hours"
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
    return render_template("channels.html",
        page="channels",
        channels=channels_list,
        channel_stats=idx_channels,
        job=jobs,
        paused=is_paused(),
        scanning=idx_status.get('scanning', False),
    )


@app.route("/logs")
def logs():
    lines = request.args.get("lines", 150, type=int)
    log_text = get_log_tail(lines)
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
    if url and ("youtube.com" in url or "youtu.be" in url):
        with open(CHANNELS_FILE, "a") as f:
            f.write(f"\n{url}\n")
    return redirect(request.referrer or url_for("channels"))


@app.route("/remove", methods=["POST"])
def remove_channel():
    url = request.form.get("url", "").strip()
    if url:
        try:
            with open(CHANNELS_FILE, "r") as f:
                lines = f.readlines()
            with open(CHANNELS_FILE, "w") as f:
                for line in lines:
                    if line.strip() != url:
                        f.write(line)
        except FileNotFoundError:
            pass
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


@app.route("/run/<mode>", methods=["POST"])
def run(mode):
    if mode == "update":
        if jobs["status"] != "running":
            thread = threading.Thread(target=run_update, daemon=True)
            thread.start()
        return redirect(request.referrer or url_for("settings"))

    if mode not in ("fast", "full"):
        return "Invalid mode", 400
    if jobs["status"] == "running":
        return redirect(request.referrer or url_for("dashboard"))
    if is_paused():
        set_paused(False)

    thread = threading.Thread(target=run_download, args=(mode,), daemon=True)
    thread.start()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/run/single", methods=["POST"])
def run_single():
    url = request.form.get("url", "").strip()
    if not url:
        return "Missing URL", 400
    if jobs["status"] == "running":
        return redirect(request.referrer or url_for("channels"))
    if is_paused():
        set_paused(False)
    thread = threading.Thread(target=run_single_channel, args=(url,), daemon=True)
    thread.start()
    return redirect(request.referrer or url_for("channels"))


@app.route("/logs/clear", methods=["POST"])
def clear_logs():
    try:
        with open(LOG_FILE, "w") as f:
            f.truncate(0)
    except Exception as e:
        print(f"Error clearing logs: {e}")
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
    from indexer import get_channel_videos, get_index_status, get_db
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
    from indexer import start_background_index, get_index_status

    status = get_index_status()
    if status['scanning']:
        return jsonify({"status": "already scanning"})

    start_background_index(force=True)
    return jsonify({"status": "started"})


@app.route("/api/index-status")
def api_index_status():
    from indexer import get_index_status
    return jsonify(get_index_status())


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
        from indexer import init_db, start_background_index
        init_db()
        start_background_index()
        print("📚 Background indexer started")
    except Exception as e:
        print(f"⚠️ Indexer init failed: {e}")

    app.run(host="0.0.0.0", port=5000, debug=False)
