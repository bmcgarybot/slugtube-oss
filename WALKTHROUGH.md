# SlugTube Feature Walkthrough
*Updated June 22, 2026*

Every button, every feature, and why it exists.

---

## Dashboard (📊)

Your home base. Everything at a glance.

### Latest Downloads
- **Where:** Top of dashboard, full-width grid
- **What:** Last 8 videos that came through, with thumbnails, titles, channel names, and duration
- **Click** any video to play it instantly

### Stat Cards
Four cards across the top with colored icons:

| Card | What It Shows |
|------|---------------|
| **📺 Subscribed Channels** | How many channels are in your channels.txt |
| **🎬 Videos in Library** | Total video files across all channel folders on disk |
| **📋 Archive Entries** | Video IDs tracked in the archive (these never re-download) |
| **💾 Library Size** | Total disk space used by your video library, plus current yt-dlp version |

#### Sync Archive Button
- **Where:** Inside the Archive Entries stat card — only appears when archive count < videos on disk
- **What it does:** Scans your video files, extracts YouTube IDs, and adds any missing ones to the archive file
- **Why:** If you imported videos from another app or manually added files, yt-dlp might not know about them and could re-download duplicates. This button closes that gap.
- **How:** Click it → confirmation dialog shows how many IDs are missing → confirms → toast notification with results

### Quick Status Bar
- Cookie health, last check time, last full index time, yt-dlp version
- Green = good, orange = warning, red = problem

### Action Buttons (top of page)

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **Pause / Resume** | Stops/starts all scheduled downloads globally | When you need bandwidth for something else, or want to stop downloads temporarily without disabling cron |
| **Fast Check** | Scans last 50 videos per channel | Quick way to catch new uploads without scanning entire channel histories |
| **Full Index** | Scans complete history for all channels | Thorough — catches videos that RSS/fast checks miss, especially older uploads |
| **Cancel All** | Kills the running download and clears the queue | Emergency stop if something's wrong (bad cookie, wrong channel, etc.) |
| **Refresh Stats** | Re-scans the library to update stat cards | Stats are cached for speed — this forces a fresh count of files and sizes |
| **🔧 Merge # Folders** | Finds folders like `ChannelName#` and merges them into `ChannelName` | YouTube sometimes assigns channel names with a trailing `#`. yt-dlp creates a separate folder for these. This button finds those split folders, moves all videos into the correct folder, and removes the empty duplicate. Prevents your library from having two folders for the same channel. |

### Largest Channels
- Horizontal bar chart showing your biggest channels by storage
- Click "View all →" to jump to the Channels page

### Recent Activity
- Styled feed of recent download/indexer events with color-coded icons:
  - ↓ Downloads (blue)
  - ✓ Indexed/success (green)
  - ✕ Errors (red)
  - — Skipped/already downloaded (dim)

### Schedule Info
- Bottom bar showing current automation schedule (fast check interval, full index day/time, yt-dlp update time)

---

## Channels (📺)

Single unified page. No more bouncing between separate channel and library views.

### Browse View (default)

Two display modes — toggle with the icons in the top right:

- **⊞ Grid view** (default): Poster cards with channel names, video count, size, and status badge. Click any card to dive into that channel.
- **☰ Table view**: Traditional sortable list with video counts, sizes, subscription status, NFO/art indicators, category, and action buttons per row.

### Mix Mode
- Click the **Mix** button in the top right to enter multi-select mode
- Select multiple channels by clicking their cards
- A bottom bar appears with **Shuffle Play** — plays random videos from your selected channels
- Great for "play me something from any of these 5 channels"

### Add Channel
- Form at the top of the page
- Paste a YouTube channel or playlist URL + optional display name
- Toggle "Playlists" checkbox to also index that channel's curated YouTube playlists
- Gets added to channels.txt immediately

### Click a Channel → Channel Detail View

Everything loads in-place with no page navigation:

#### Channel Strip
- Horizontal scrollable row of all your channels with poster thumbnails
- Click any to quick-switch between channels without going back to the browse view
- Active channel is highlighted with an accent border

#### Hero Banner
- Full-width channel artwork (banner image) with gradient fade into the page background
- Falls back to a shorter colored bar if no banner art exists

#### Stats Bar
- Video count, total size on disk, subscription status

#### Latest Video Preview
- Small card on the right showing the most recent video with thumbnail, title, and date
- Click to play it directly

#### Playlist Cloud
- If the channel has indexed playlists, shows clickable tags for each playlist with video counts
- Click a playlist tag to see just those videos
- Link to full playlists overview page

#### Action Buttons (Channel Detail)

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **Quick Check** | Scans this specific channel for new videos (last 50) | Faster than checking all channels — just check the one you care about right now |
| **Download** | Downloads all videos for this channel (full history) | When you add a new channel and want everything, not just recent |
| **Reindex** | Rebuilds this channel's database entries from files on disk | If the database gets out of sync with what's actually in the folder (manual file moves, imports, etc.) |
| **Reindex Playlists** | Fetches playlist data from YouTube for this channel | Updates which videos belong to which playlists — YouTube playlists change over time |
| **Fetch Art** | Grabs poster and banner artwork from YouTube | Gets the profile picture and channel banner for display in the UI |
| **Toggle Playlists** | Enable/disable playlist indexing for this channel | Some channels have useful playlists, others don't — your choice per channel |
| **Unsubscribe** | Removes from channels.txt (keeps files on disk) | Stop tracking new videos without deleting what you already have |
| **Delete** | Removes the channel folder and all its files (behind confirmation) | Nuclear option — permanently removes everything for this channel |

### Video Grid / List

Two view modes for videos within a channel:

- **⊞ Grid view** (default): Thumbnail cards with title, date, duration, view count
- **☰ List view**: Compact table with sortable columns

#### Sort Options
| Sort | What It Does |
|------|--------------|
| Newest | Most recent upload date first |
| Oldest | Oldest upload date first |
| Title A-Z | Alphabetical by title |
| Largest | Biggest file size first |
| Smallest | Smallest file size first (great for finding shorts/junk) |
| Longest | Longest duration first |
| Shortest | Shortest duration first (great for finding shorts) |
| Most Viewed | Highest view count first |

#### Watch Progress Indicators
- **Blue progress bar** at bottom of thumbnail = partially watched (shows how far you got)
- **"Watched" badge** = completed
- Watched videos are dimmed slightly so unwatched ones stand out

#### Bulk Select Mode
- Toggle the **Select** checkbox in the toolbar to enter bulk mode
- Action bar appears with:
  - **Select All / Select None** — quick toggle
  - **Add to Playlist** — create custom playlists from selected videos
  - **Delete Selected** — modal confirmation with option to also exclude from future downloads (default checked)

#### Pagination
- Large channels paginate automatically
- Page controls at the bottom (Previous / Page X of Y / Next)

### URL Hash Routing
- URLs like `/channels#Channel Name` link directly to a channel
- Browser back/forward navigates between channels
- Bookmarkable — share a link to a specific channel view

---

## Video Player (/watch)

### Controls
- Standard HTML5 video player with full controls
- **Resume playback** — automatically picks up where you left off
- **Progress auto-saved** every 10 seconds (no "save" button needed)

### Action Bar

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **Mark Watched** | Toggles watched/unwatched status | Manually mark something as watched (or unwatched to re-watch later) |
| **⏮ Prev** | Go to the previous video in the channel | Navigate sequentially through a channel's videos |
| **⏭ Next** | Go to the next video in the channel | Continue watching through a channel's content |
| **Autoplay: ON/OFF** | When ON, automatically plays the next video after the current one ends | Lean-back viewing — set it and forget it |
| **Shuffle: ON/OFF** | When ON + Autoplay ON, picks a random video from the same channel instead of sequential | Discovery mode — great for channels with huge back catalogs |
| **🗑 Delete** | Permanently remove this video file (behind confirmation dialog) | Clean up videos you don't want to keep |

### Autoplay Countdown
When a video ends with Autoplay ON:
- Gradient overlay appears at the bottom of the player
- Shows "Up next in 5s... [video title]"
- **Cancel** button stops the countdown
- **Play Now** button skips the wait
- If Shuffle is ON, the "next" video is random from the same channel

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space / K | Play / Pause |
| J / ← | Seek back 10 seconds |
| L / → | Seek forward 10 seconds |
| N | Next video |
| P | Previous video |
| F | Toggle fullscreen |
| M | Mute / Unmute |

---

## Library (/library)

- Full channel grid with poster art
- Browse all videos across all channels in one view
- **Full-text search** (SQLite FTS5) across video titles and descriptions — instant results

### Global Search Bar
- Always visible in the sidebar on every page
- Type a query and hit Enter → routes to Library with filtered results
- Searches across all video titles and descriptions simultaneously

---

## Playlists (/playlists)

- Overview of all indexed YouTube playlists across all channels
- Click a playlist to see its videos
- Playlists are indexed per-channel (enable via channel settings or the Playlists checkbox when adding)

---

## Recent (/library/recent)

- Recently downloaded videos in reverse chronological order
- Quick way to see what SlugTube just grabbed

---

## History (/history)

- Watch history with timestamps
- Resume any previously watched video with one click
- Shows watch progress (percentage completed)

---

## Cookie Health Indicator

### Where
- **Green/orange/red dot** next to "SlugTube" in the sidebar — visible on every page

### What It Means
- 🟢 **Green** — Cookies are healthy, downloads will work
- 🟡 **Orange** — Cookies getting old (>7 days) or low-level warning detected
- 🔴 **Red** — Cookies expired, missing, or YouTube actively rejected them

### How It Works
- Checks cookie file age and expiration dates
- Scans recent yt-dlp logs for YouTube's "cookies are no longer valid" server-side rejection message
- Ignores errors from before the last cookie refresh (10-minute grace period to avoid false alarms)
- Hover over the dot for details (age in days, rejection timestamp, etc.)
- Auto-checks every 60 seconds

---

## Cookies (🍪)

- **Paste area** — paste your exported cookies.txt content directly
- **Auto-fixes tabs** — if your browser mangles tab characters into spaces when copying, SlugTube automatically repairs the formatting
- **Save** — writes to `/config/Cookie/cookies.txt`

### Getting Cookies That Last
1. Export from Firefox (not Chrome/Brave) OR export from Brave but **close it completely after**
2. Use the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) browser extension
3. Close the browser completely after exporting
4. Don't browse YouTube in that same browser after exporting (the session token gets rotated)

---

## Health Check (🩺)

Per-channel video file integrity scanner. Finds corrupted, truncated, or broken video files using ffmpeg.

### Channel List
Each channel shows: files checked, status (healthy/broken count), and action buttons.

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **🩺 Full Scan** | Decodes every frame of every video file using ffmpeg | Thorough corruption detection — catches partial corruption mid-file. Slow but comprehensive. |
| **⚡ Quick** | Container-level check (reads headers, doesn't decode frames) | Fast sanity check — catches truncated files and broken containers without the full decode time |
| **🔧 Fix N** | Re-downloads all broken files for this channel (appears only when broken files exist) | One-click repair: deletes the broken files, removes their IDs from the archive, and queues re-download |

### Live Progress Panel
- Progress bar with percentage
- Stats: Checked / Healthy / Broken / Already Checked (skipped)
- Current file being scanned
- Collapsible scan log for detailed output

### Channel Detail Panel
- Click any channel name to see per-file results
- Broken files listed with filename, size, error description, and individual **Fix** button
- Healthy files listed in a collapsible section
- **Results are saved per-file** — you can stop anytime and pick up where you left off
- Runs at lowest CPU priority so streaming isn't affected

### Incomplete Downloads Scanner

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **Scan for .temp files** | Finds interrupted/partial downloads (.temp, .part files) across your library | These leftover files from interrupted downloads prevent yt-dlp from re-downloading the video because it thinks the file already exists |
| **Fix** (per file) | Deletes the incomplete file and removes its ID from the archive | Lets yt-dlp grab a fresh copy on the next scheduled check |
| **Fix All** | Fixes every incomplete file found in one shot | Bulk cleanup after a crash, power outage, or network interruption |

---

## Themes (🎨)

### Where
- **Sidebar** — theme dropdown + 🌙/☀️ dark/light mode toggle
- **Settings page** — visual preview cards with color swatches

### Available Themes

| Theme | Dark Mode | Light Mode |
|-------|-----------|------------|
| **Midnight** | Deep dark (default) | Cool blue-gray |
| **Jellyfin** | Deep navy | Blue-tinted |
| **Playnite** | Purple gaming aesthetic | Lavender-purple |
| **AMOLED** | True black (#000000) | N/A (dark only) |

- Each light variant is **tinted** to match its theme (not just plain white)
- Theme + mode saved to localStorage — loads before paint, no flash

---

## Settings (⚙️)

### Theme Selector
- Visual cards showing color swatches for each theme
- Click to apply immediately

### Quality Profile
| Setting | Options | Why |
|---------|---------|-----|
| **Resolution** | 720p / 1080p / 1440p / 4K / Best Available | Controls max resolution — lower = smaller files, faster downloads |
| **Container Format** | MP4 / MKV / WebM | MP4 for compatibility, MKV for features, WebM for size |
| **Skip Shorts** | On/Off | Automatically skip videos under 60 seconds (YouTube Shorts clutter) |

### Metadata & Files
| Setting | What It Does | Why |
|---------|-------------|-----|
| **Embed Subtitles** | Bakes subtitles into the video file | One file, always has subs, works on any player |
| **Separate .srt Files** | Saves subtitle files alongside the video | For players that prefer external subtitle files |
| **Embed Metadata** | Stores title, description, upload date inside the file | File carries its own context even outside SlugTube |
| **Embed Thumbnail** | Poster art embedded in the video file | Shows thumbnail in file browsers and media players |
| **Write .nfo Files** | Creates Jellyfin/Kodi metadata files | Required for proper media server library scanning |
| **Write Thumbnail Files** | Separate .jpg episode thumbnails | For media servers that read external thumbnail files |

### Schedule
Configurable cron schedules for all three automated tasks:

| Schedule | Default | What It Does |
|----------|---------|--------------|
| **Fast Check** | Every 6 hours | Scans last 50 videos per channel for new uploads |
| **Full Index** | Sundays 3 AM | Deep scan of entire channel history |
| **yt-dlp Update** | Daily 1 AM | Updates the video downloader to latest version |

Each schedule has:
- **Enable/Disable checkbox** — toggle without losing the cron expression
- **Preset dropdown** — common intervals (every 2h, 4h, 6h, 12h, daily, weekly, etc.)
- **Custom cron input** — type any valid cron expression for full control
- **Timezone picker** — searchable dropdown of all timezones so schedules run in your local time

Changes take effect immediately — no container restart needed.

### System Info
- Current yt-dlp version with **Update Now** button
- Archive entry count
- Last fast check and full index timestamps

### Manual Actions

| Button | What It Does | Why It Exists |
|--------|-------------|---------------|
| **Pause / Resume Downloads** | Temporarily stops all scheduled downloads | Bandwidth management — stop downloads without disabling the schedule |
| **Check for New Videos** | Same as Fast Check from Dashboard | Quick scan of recent videos across all channels |
| **Download Full History** | Same as Full Index from Dashboard | Deep scan of complete channel histories |
| **Update Downloader** | Updates yt-dlp to latest version | YouTube changes frequently — keeping yt-dlp current prevents download failures |
| **Download Channel Art** | Fetches poster and banner images for ALL channels | Batch operation so you don't have to click Fetch Art per channel |
| **Rebuild Library Index** | Rescans all video files on disk and rebuilds the database | Recovery tool: if the database gets corrupted or out of sync, this rebuilds it from your actual files. Does NOT download anything. |
| **Clean Ghost Entries** | Removes database entries for channels no longer in channels.txt and deduplicates | Cleanup after unsubscribing or removing channels — keeps the database tidy. Does not delete any video files. |
| **Update SlugTube** | Pulls latest code from GitHub | One-click update: git pulls new code, merges channels.txt without overwriting yours, and applies changes. Just refresh your browser after. |
| **Show Feature Walkthrough** | Restarts the interactive UI tour | Guided tour of the interface for new users or a refresher |

### Import Channels (3 methods)

All three import methods show a preview table with checkboxes and duplicate detection before adding anything. Nothing is overwritten.

| Method | What It Does | Best For |
|--------|-------------|----------|
| **Scan Existing Library** (Recommended) | Reads `.info.json` files from a folder to discover channels | Migrating from any yt-dlp based setup, TubeArchivist, or PinchFlat without needing their database files |
| **TubeArchivist Backup ZIP** | Parses a TA backup archive | Migrating from TubeArchivist with full channel metadata |
| **PinchFlat Database** | Reads a PinchFlat SQLite database | Migrating directly from PinchFlat |

---

## Logs (📋)

- **Filter tabs:** All / Errors Only / Downloads / Skipped
- **Line count:** 50 / 150 / 500 / 1000 / Full
- **Export** — download the log as a timestamped `.txt` file
- **Clear** — wipe all logs
- Smart error filtering that strips noise

---

## Sidebar

Present on every page:

| Element | What It Does |
|---------|--------------|
| **SlugTube logo + cookie dot** | Logo links to Dashboard; colored dot shows cookie health at a glance |
| **Search bar** | Global search — type and hit Enter to search all video titles/descriptions |
| **Navigation links** | Dashboard, Channels, Library, Playlists, Recent, History, Health Check, Logs, Cookies, Settings |
| **Theme dropdown** | Switch between Midnight, Jellyfin, Playnite, AMOLED |
| **🌙/☀️ toggle** | Switch between dark and light mode |

---

## Responsive Design

- **Desktop:** Full sidebar navigation + multi-column layouts
- **Medium screens:** Sidebar collapses, some table columns hidden
- **Mobile:** Card-based layouts, stacked forms, swipeable channel strips
- **Dark theme throughout** — respects your theme choice at every breakpoint

---

## update.bat / rebuild.bat (Windows)

### update.bat
Double-click from File Explorer:
1. `git pull` (gets latest code)
2. Restarts the Docker container
3. Tells you to Ctrl+Shift+R (hard refresh browser)

### rebuild.bat
For when you need a fresh Docker image:
1. Stops the container
2. Rebuilds the Docker image from scratch
3. Restarts everything

---

## Safety Features

- Blank or malformed channel entries in channels.txt are silently skipped (won't crash downloads)
- Channels with `#` in their YouTube-assigned name are handled correctly (see Merge # Folders)
- Your local `channels.txt` is never overwritten by updates — new channels from the repo are merged in
- Confirmation modals on ALL destructive actions (delete, unsubscribe, clear)
- Ghost cleanup only removes database entries, not files (unless the folder is already empty)
- Download script properly handles folder names with spaces using quoted arguments
- SQLite connections use a 30-second timeout to prevent "database is locked" errors during heavy operations
- Health check scans run at lowest CPU priority so streaming and playback aren't affected
- Incomplete download scanner finds and fixes .temp/.part files that would otherwise block re-downloads

---

## Live Indexing

- Videos are indexed into the database immediately after download completes
- Uses yt-dlp's `--exec after_move` hook to POST to the indexing API
- No need to wait for a full reindex — new videos appear instantly in the UI

---

*Built with yt-dlp, Flask, SQLite, and stubbornness. 🐌*
