# SlugTube Feature Walkthrough
*Updated May 28, 2026*

---

## Dashboard (📊)

Your home base. Everything at a glance.

### Latest Downloads
- **Where:** Top of dashboard, full-width grid
- **What:** Last 8 videos that came through, with thumbnails, titles, channel names, and duration
- **Click** any video to play it

### Stat Cards
- Four cards with colored icons: Subscribed Channels (📺), Videos in Library (🎬), Archive Entries (📋), Library Size (💾)

### Quick Status Bar
- Cookie health, last check time, yt-dlp version
- Green = good, orange = warning, red = problem

### Largest Channels
- Horizontal bar chart showing your biggest channels by storage
- Click "View all" to go to Channels page

### Recent Activity
- Styled feed of recent indexer/download events with color-coded icons

### Action Buttons (top right)
- **Quick Check** — scan last 50 videos per channel (fast)
- **Full Index** — scan everything (slow, thorough)
- **Pause/Resume** — stop/start all downloads

---

## Channels (📺)

Single unified page. No more bouncing between Channels and Library.

### Browse View (default)
- **Grid view** (default): poster cards with channel names. Click the ⊞ icon to switch views.
- **Table view**: traditional list with video counts, sizes, subscription status

### Click a Channel
Everything loads in-place, no page navigation:
- **Channel strip** at top — horizontal scroll of all channels for quick switching
- **Hero banner** — channel artwork with gradient fade
- **Stats bar** — video count, total size, subscription status
- **Action buttons:**
  - Quick Check — scan this channel for new videos
  - Download — download all videos
  - Reindex — rebuild this channel's database entries
  - Fetch Art — grab poster/banner from YouTube
  - Unsubscribe — remove from channels.txt
  - ⋮ menu — Delete (destructive, behind confirmation)

### Video Grid
- **Grid view** (default) or **List view** — toggle with icons
- **Sort:** Newest, Oldest, Title A-Z, Largest, Longest
- **Watch progress bars** on partially-watched videos
- **"Watched" badge** on completed videos
- **Click** any video to play

### Add Channel
- Form at the bottom of the page
- Paste a YouTube channel/playlist URL + display name
- Gets added to channels.txt

### URL Hash Routing
- URLs like `/channels#Chilling Tales for Dark Nights` work
- Browser back/forward navigate between channels
- Bookmarkable

---

## Video Player (/watch)

### Controls
- Standard HTML5 video player with full controls
- **Resume playback** — picks up where you left off
- **Progress auto-saved** every 10 seconds

### Action Bar
- **Mark Watched** — toggle watched status
- **⏮ Prev / ⏭ Next** — navigate within channel
- **Autoplay: ON/OFF** — when ON, plays next video after current ends
- **Shuffle: ON/OFF** — when ON, picks random video from same channel instead of sequential
- **🗑 Delete** — permanently remove video (behind confirmation dialog)

### Autoplay Countdown
- When a video ends with Autoplay ON:
  - Gradient overlay appears at bottom of player
  - Shows "Up next in 5s... [video title]"
  - **Cancel** stops it, **Play Now** skips the countdown
  - If Shuffle is ON, picks a random video from the channel

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| Space / K | Play/Pause |
| J / ← | Seek back 10s |
| L / → | Seek forward 10s |
| N | Next video |
| P | Previous video |
| F | Fullscreen |
| M | Mute/Unmute |

---

## Cookie Health Indicator

### Where
- **Green/orange/red dot** next to "SlugTube" in the sidebar, visible on every page

### What it means
- 🟢 **Green** — cookies healthy, downloads will work
- 🟡 **Orange** — cookies getting old (>7 days) or low-level warning
- 🔴 **Red** — cookies expired, missing, or YouTube rejected them (re-export needed)

### Hover
- Hover over the dot for details (age, rejection timestamp, etc.)
- Checks every 60 seconds automatically
- Also scans logs for YouTube's "cookies are no longer valid" server-side message

---

## Cookies (🍪)

- **Paste area** — paste your exported cookies.txt content
- **Auto-fixes tabs** — if your browser mangles tab characters into spaces, SlugTube fixes them automatically
- **Save** — writes to `/config/Cookie/cookies.txt`

### Getting cookies that last:
1. Export from Firefox (not Chrome/Brave) OR export from Brave but close it after
2. Use "Get cookies.txt LOCALLY" browser extension
3. Close the browser completely after exporting
4. Don't browse YouTube in that browser after exporting

---

## Themes (🎨)

### Where
- **Sidebar** — theme dropdown + 🌙/☀️ mode toggle
- **Settings page** — visual preview cards with color swatches

### Available Themes (7 total)
| Theme | Dark | Light |
|-------|------|-------|
| Midnight | Deep dark (default) | Cool blue-gray |
| Jellyfin | Deep navy | Blue-tinted |
| Playnite | Purple gaming | Lavender-purple |
| AMOLED | True black | N/A (dark only) |

- Each light variant is **tinted** to match its theme (not plain white)
- Theme + mode saved to localStorage, loads before paint

---

## Settings (⚙️)

### Download Settings
- Quality profile, subtitle embedding, metadata options
- Schedule display (fast check + full index cron times)

### Maintenance Buttons
- **⚡ Run Fast Check** — scan recent videos across all channels
- **📚 Run Full Index** — deep scan everything
- **🔄 Update yt-dlp** — update the downloader
- **🎨 Fetch All Channel Art** — grabs posters/banners for ALL channels at once
- **📂 Re-Index Library** — rebuild the entire database from disk

### Import Channels (3 methods)
1. **Scan Existing Library** — reads your media folders and finds channels from `.info.json` files
2. **TubeArchivist Backup ZIP** — upload a TA backup, extracts channel data
3. **PinchFlat SQLite DB** — reads PinchFlat's database directly

All three show a preview table with checkboxes and duplicate detection before adding.

---

## Logs (📋)

- **Filter buttons:** All, Errors Only, Downloads, Skipped
- **Line count:** 50 / 150 / 500 / 1000 / Full
- **Export** — download log file
- **Clear** — wipe logs

---

## History (📜)

- Watch history with timestamps
- Resume any previously watched video

---

## Bulk Actions (Channels page)

- **Select multiple videos** using checkboxes
- **Delete** — modal confirmation dialog with checkbox "Also exclude from future downloads" (default checked)
- **Add to Playlist** — create custom playlists from selected videos

---

## update.bat

Double-click from File Explorer. It does everything:
1. `git pull` (gets latest code)
2. Copies files to Docker mount
3. Smart-merges channels.txt (adds new channels without overwriting your local ones)
4. Restarts the Docker container
5. Tells you to Ctrl+Shift+R

No need to run `git pull` separately.
