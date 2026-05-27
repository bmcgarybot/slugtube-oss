# 🐌 SlugTube

**A self-hosted YouTube channel archiver with a dark-themed web UI.**

SlugTube automatically downloads, organizes, and indexes YouTube channels into a Jellyfin/Kodi-compatible library. Think PinchFlat or TubeArchivist, but simpler — one `docker compose up` and you're archiving.

---

## ✨ Features

- **📊 Dashboard** — Live download status, job queue, disk usage, cookie health, recent activity
- **🎬 Library** — Channel grid with poster art, video browsing, full-text search
- **📺 Unified Channels** — One table for everything: Active (subscribed + on disk), Subscribed (pending), Library (untracked). Status badges, category column, all actions in one row
- **📋 Playlist Support** — Index YouTube playlists, browse by playlist, playlist overview pages
- **▶️ Video Player** — Built-in player with resume playback, watch history, keyboard shortcuts
- **🍪 Cookie Manager** — Paste & manage YouTube cookies from the web UI (with tab-fix auto-repair)
- **⚙️ Settings** — Quality profiles (720p–4K), format selection, metadata toggles
- **⏰ Scheduled Downloads** — Cron-based fast checks, full indexing, yt-dlp auto-updates
- **🎨 Channel Art** — Auto-fetches poster/banner artwork + manual "Fetch Art" button
- **📁 Jellyfin-Compatible** — `Season YYYY` folder structure, `.nfo` files, embedded metadata
- **🔍 Full-Text Search** — SQLite FTS5 index across all video titles and descriptions
- **📜 Watch History** — Track watched/in-progress videos with resume positions
- **☑️ Bulk Operations** — Select multiple videos → bulk delete (removes files + blocks re-download) or bulk exclude (blocks without deleting)
- **🗑️ Video & Channel Management** — Delete individual videos or entire channel folders from the UI
- **📋 Logs** — Real-time log viewer with filters (All / Errors / Downloads / Skipped), full-log view, export to file
- **⏸️ Pause/Resume** — Pause all downloads globally from the dashboard
- **⛔ Cancel** — Kill running downloads and clear the job queue
- **📱 Responsive** — Full desktop layout, medium breakpoint (hides columns), mobile card layout
- **🧹 Auto-Cleanup** — Ghost DB entries auto-purged when folders disappear. Folders auto-created for subscribed channels.

## 🚀 Quick Start

```bash
# Clone the repo
git clone https://github.com/youruser/slugtube.git
cd slugtube

# Copy the example environment file and edit it
cp .env.example .env
# Edit .env to set your paths (at minimum: SHOWS_DIR and CONFIG_DIR)

# Start SlugTube
docker compose up -d

# Open the dashboard
open http://localhost:5000
```

That's it. On first run, SlugTube will:
1. Create default config files
2. Show a "Getting Started" page if no channels are configured
3. Update yt-dlp to the latest nightly build
4. Start the web dashboard on port 5000

## ⚙️ Configuration

All configuration is done through environment variables in `.env` (or `docker-compose.yml`).

### Required Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOWS_DIR` | `/shows` | Where videos are stored (mount your media drive here) |
| `CONFIG_DIR` | `/config` | Config, database, logs, cookies, archive |

### Schedule (Cron Expressions)

| Variable | Default | Description |
|----------|---------|-------------|
| `FAST_CRON` | `0 */6 * * *` | Fast check — latest 50 videos per channel (every 6 hours) |
| `FULL_CRON` | `0 3 * * 0` | Full index — entire channel history (Sundays 3 AM) |
| `UPDATE_CRON` | `0 1 * * *` | yt-dlp auto-update (daily 1 AM) |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Container timezone |
| `WEB_PORT` | `5000` | Web UI port (inside container) |
| `PUID` | `1000` | User ID for file permissions |
| `PGID` | `1000` | Group ID for file permissions |

## 📺 Adding Channels

### Migrating from TubeArchivist or PinchFlat

SlugTube has built-in import tools on the **Settings** page. Three methods:

#### 1. Scan Existing Library (Recommended)

If you already have a media folder full of downloaded videos (from TubeArchivist, PinchFlat, or plain yt-dlp), just point SlugTube at it:

1. Go to **Settings → Import Channels**
2. Enter the path to your media folder (e.g. `/youtube/` or `/downloads/shows/`)
3. Click **Scan** — SlugTube reads `.info.json` files to find channel names and URLs
4. Review the list, check the ones you want, click **Import Selected**

This works with any yt-dlp-based folder structure. No backup files needed.

#### 2. TubeArchivist Backup ZIP

1. In TubeArchivist: **Settings → Backup → Create Backup**
2. Download the backup ZIP
3. In SlugTube: **Settings → Import → TubeArchivist Backup → Upload**
4. SlugTube extracts your subscribed channels from the metadata
5. Review and import

#### 3. PinchFlat Database

1. Find your PinchFlat database file: `pinchflat.db` (in your PinchFlat config directory, usually `/config/`)
2. In SlugTube: **Settings → Import → PinchFlat Database → Upload**
3. SlugTube reads your sources from the SQLite database
4. Review and import

All import methods:
- **Never overwrite** your existing channels — only append new ones
- **Detect duplicates** by URL, channel name, and channel ID
- **Auto-create folders** for imported channels
- Show a preview with checkboxes so you can pick exactly what to import

> **Note on file naming:** TubeArchivist and PinchFlat use different file naming conventions. Imported channels will download NEW videos in SlugTube's format. Existing files from your old app will still be indexed and playable, but won't have the `Season YYYY/` folder structure until re-downloaded.

### From the Web UI

1. Go to **Channels** page
2. Paste a YouTube channel URL (e.g. `https://www.youtube.com/@ChannelName/videos`)
3. Optionally set a display name and enable playlist indexing
4. Click **+ Add Channel**

Channels appear immediately with ✅ Active status. A folder is auto-created on disk.

### From `channels.txt`

Edit the `channels.txt` file in your config directory:

```
# Format: URL or URL|DisplayName or URL|DisplayName|playlists
#
# Section headers (optional):
# # --- Horror ---
# # --- Comedy ---

https://www.youtube.com/@SomeChannel/videos
https://www.youtube.com/@AnotherOne/videos|My Custom Name
https://www.youtube.com/@WithPlaylists/videos|Channel Name|playlists

# You can also add individual playlists:
https://youtube.com/playlist?list=PLxxxxxxxx|Playlist Name
```

The `|playlists` flag tells SlugTube to also index the channel's curated YouTube playlists.

## ☑️ Bulk Operations

On channel pages, click the **☑️ Select** button to enter bulk mode:

- **Select All** — toggle all visible videos
- **🗑️ Delete Selected** — removes files from disk AND adds to archive (blocks re-download)
- **🚫 Exclude from Future** — adds to archive without deleting existing files (blocks future downloads)
- **Sort by Smallest/Shortest** — find junk content fast

Useful for cleaning up shorts, intros, or content you don't want in your library.

## 📋 Playlist Support

SlugTube can index a channel's curated YouTube playlists and let you browse videos by playlist:

1. **Enable** — Add `|playlists` to a channel entry in `channels.txt`, or toggle the 📋 button on the Channels page
2. **Index** — Click "🎵 Reindex Playlists" on a channel page (fetches playlist data from YouTube)
3. **Browse** — Playlists appear on the channel page as a card grid with thumbnails

Playlists only show videos you've already downloaded — they're an organizational layer, not a download trigger.

## 🍪 Cookies

YouTube requires cookies for:
- Age-restricted content
- Avoiding "Sign in to confirm you're not a bot" errors
- Higher rate limits

**To set up cookies:**
1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) in Chrome/Edge
2. Go to youtube.com (make sure you're logged in)
3. Export cookies with the extension
4. Paste them on the **Cookies** page in SlugTube

Cookies expire periodically — refresh them when downloads start failing.

## 📁 Folder Structure

SlugTube organizes downloads in a Jellyfin/Kodi-compatible structure:

```
/shows/
├── Channel Name/
│   ├── poster.jpg           # Channel avatar
│   ├── banner.jpg           # Channel banner
│   ├── tvshow.nfo           # Jellyfin metadata
│   ├── Season 2024/
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].mp4
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].info.json
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].jpg
│   │   └── s2024e20240315 - Video Title [VIDEO_ID].en.srt
│   └── Season 2025/
│       └── ...
└── Another Channel/
    └── ...
```

## 📋 Log Viewer

The logs page includes:
- **Filter tabs** — All / ❌ Errors Only / ⬇️ Downloads / ⏭️ Skipped
- **Full log** — View the entire log file (not just last 200 lines)
- **📥 Export** — Download logs as a timestamped `.txt` file
- Smart error filtering that strips noise (already-downloaded notifications, skip messages)

## 🔧 Development

```bash
# Run without Docker (requires Python 3.10+, yt-dlp, ffmpeg)
cd web
pip install flask
python app.py

# The app reads these environment variables:
export SHOWS_DIR=/path/to/your/shows
export CONFIG_DIR=/path/to/your/config
```

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/cool-thing`)
3. Commit your changes (`git commit -m 'Add cool thing'`)
4. Push to the branch (`git push origin feature/cool-thing`)
5. Open a Pull Request

## 📄 License

[MIT](LICENSE) — do whatever you want with it.

---

*Built with yt-dlp, Flask, SQLite, and stubbornness.*
