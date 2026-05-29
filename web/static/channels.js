/* SlugTube Unified Channels Page — channels.js */

var _st = {
    current: null,   // selected channel name
    data: null,      // API response for current channel
    sort: 'newest',
    jobRunning: false,
    page: 1,
    perPage: 60
};

function initChannelsPage(jobRunning) {
    _st.jobRunning = jobRunning;
    applyGradients();

    // Restore browse view preference (default: grid)
    var browseView = localStorage.getItem('slugtube-channel-browse-view') || 'grid';
    setBrowseView(browseView);

    // Check URL hash for auto-select
    var hash = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (hash) {
        selectChannel(hash);
    }

    // Handle browser back/forward
    window.addEventListener('popstate', function() {
        var h = decodeURIComponent(location.hash.replace(/^#/, ''));
        if (h) {
            selectChannel(h);
        } else {
            goBackToBrowse();
        }
    });
}

/* ═══ Gradient generator ═══ */
function getChannelGradient(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    var h = Math.abs(hash % 360);
    return 'linear-gradient(135deg, hsl(' + h + ',55%,35%) 0%, hsl(' + ((h + 40) % 360) + ',45%,25%) 100%)';
}

function applyGradients() {
    document.querySelectorAll('.poster-fallback').forEach(function(el) {
        var card = el.closest('[data-name]');
        if (card) {
            el.style.background = getChannelGradient(card.getAttribute('data-name'));
            return;
        }
        var gc = el.closest('.channel-grid-card');
        if (gc) {
            var n = gc.querySelector('.channel-grid-name');
            if (n) el.style.background = getChannelGradient(n.textContent.trim());
        }
    });
}

/* ═══ Toast ═══ */
function showToast(msg) {
    var t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function() { t.classList.add('show'); }, 10);
    setTimeout(function() { t.classList.remove('show'); setTimeout(function() { t.remove(); }, 300); }, 3000);
}

/* ═══ Format helpers ═══ */
function fmtDuration(s) {
    if (!s) return '0:00';
    s = Math.floor(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h) return h + ':' + (m < 10 ? '0' : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
    return m + ':' + (sec < 10 ? '0' : '') + sec;
}

function fmtSize(b) {
    if (!b) return '\u2014';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    for (var i = 0; i < units.length; i++) {
        if (b < 1024) return (i === 0 ? Math.floor(b) : b.toFixed(1)) + ' ' + units[i];
        b /= 1024;
    }
    return b.toFixed(1) + ' PB';
}

function fmtDate(d) {
    if (!d) return '\u2014';
    if (d.length === 8) return d.substring(0, 4) + '-' + d.substring(4, 6) + '-' + d.substring(6, 8);
    return d.substring(0, 10);
}

function fmtViews(n) {
    if (!n) return '';
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/* ═══ Browse view toggle (grid / table) ═══ */
function setBrowseView(mode) {
    var grid = document.getElementById('channels-grid-view');
    var table = document.getElementById('channels-table-view');
    var gridBtn = document.getElementById('browse-grid-btn');
    var tableBtn = document.getElementById('browse-table-btn');
    if (mode === 'table') {
        grid.style.display = 'none';
        table.style.display = '';
        tableBtn.classList.add('active');
        gridBtn.classList.remove('active');
    } else {
        grid.style.display = 'grid';
        table.style.display = 'none';
        gridBtn.classList.add('active');
        tableBtn.classList.remove('active');
    }
    localStorage.setItem('slugtube-channel-browse-view', mode);
}

/* ═══ Video view toggle (grid / list) ═══ */
function setVideoView(mode) {
    var grid = document.getElementById('video-grid');
    var list = document.getElementById('video-list-table');
    var gridBtn = document.getElementById('video-grid-btn');
    var listBtn = document.getElementById('video-list-btn');
    if (mode === 'list') {
        grid.style.display = 'none';
        list.style.display = '';
        listBtn.classList.add('active');
        gridBtn.classList.remove('active');
    } else {
        grid.style.display = 'grid';
        list.style.display = 'none';
        gridBtn.classList.add('active');
        listBtn.classList.remove('active');
    }
    localStorage.setItem('slugtube-video-view', mode);
}

/* ═══ Back to browse ═══ */
function goBackToBrowse() {
    _st.current = null;
    _st.data = null;
    history.pushState(null, '', '/channels');

    document.getElementById('channel-detail').style.display = 'none';
    document.getElementById('channel-strip-wrap').style.display = 'none';

    // Show browse views
    var browseView = localStorage.getItem('slugtube-channel-browse-view') || 'grid';
    setBrowseView(browseView);

    // Swap toggles
    document.getElementById('browse-toggle').style.display = 'flex';
    document.getElementById('video-toggle').style.display = 'none';

    // Update title
    document.querySelector('.page-header h1').innerHTML = '📺 Channels <span class="text-dim text-sm" style="font-weight:400;">' + document.getElementById('channel-count').textContent + '</span>';

    // Deactivate strip cards
    document.querySelectorAll('.channel-strip-card.active').forEach(function(c) { c.classList.remove('active'); });

    window.scrollTo(0, 0);
}

/* ═══ Select a channel ═══ */
function selectChannel(name) {
    if (_st.current === name && _st.data) return; // already showing

    _st.current = name;
    _st.sort = 'newest';
    _st.page = 1;
    document.getElementById('video-sort').value = 'newest';

    // Update URL hash
    history.pushState(null, '', '/channels#' + encodeURIComponent(name));

    // Hide browse, show detail skeleton
    document.getElementById('channels-grid-view').style.display = 'none';
    document.getElementById('channels-table-view').style.display = 'none';
    document.getElementById('browse-toggle').style.display = 'none';
    document.getElementById('video-toggle').style.display = 'flex';

    // Restore video view preference
    var videoView = localStorage.getItem('slugtube-video-view') || 'grid';
    setVideoView(videoView);

    // Show strip + detail
    document.getElementById('channel-strip-wrap').style.display = '';
    document.getElementById('channel-detail').style.display = '';

    // Highlight active strip card
    document.querySelectorAll('.channel-strip-card').forEach(function(c) {
        var isActive = c.getAttribute('data-name') === name;
        c.classList.toggle('active', isActive);
        if (isActive) {
            // Scroll into view
            c.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
    });

    // Update page title
    document.querySelector('.page-header h1').innerHTML = '📺 ' + escHtml(name);

    // Loading state
    document.getElementById('detail-stats').textContent = 'Loading...';
    document.getElementById('video-grid').innerHTML = '';
    document.getElementById('video-list-tbody').innerHTML = '';
    document.getElementById('detail-actions').innerHTML = '';
    document.getElementById('detail-playlists').innerHTML = '';

    // Fetch channel data
    fetch('/api/channel/' + encodeURIComponent(name))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (_st.current !== name) return; // user already switched
            _st.data = data;
            renderChannelDetail(data);
        })
        .catch(function(err) {
            document.getElementById('detail-stats').textContent = 'Error loading channel: ' + err;
        });

    window.scrollTo(0, 0);
}

/* ═══ Render channel detail ═══ */
function renderChannelDetail(d) {
    // Hero banner
    var hero = document.getElementById('detail-hero');
    if (d.has_banner) {
        hero.style.backgroundImage = "url('/media/banner/" + encodeURIComponent(d.name) + "')";
        hero.className = 'detail-hero';
    } else {
        hero.style.backgroundImage = 'none';
        hero.style.background = getChannelGradient(d.name);
        hero.className = 'detail-hero no-banner';
    }

    // Poster
    var posterWrap = document.getElementById('detail-poster-wrap');
    // Remove old poster img if any
    var oldImg = posterWrap.querySelector('.detail-poster-img');
    if (oldImg) oldImg.remove();

    var fb = document.getElementById('detail-poster-fb');
    document.getElementById('detail-poster-initial').textContent = d.name.charAt(0).toUpperCase();
    fb.style.background = getChannelGradient(d.name);

    if (d.has_poster) {
        var img = document.createElement('img');
        img.className = 'detail-poster-img';
        img.src = '/media/poster/' + encodeURIComponent(d.name);
        img.onerror = function() { this.style.display = 'none'; };
        posterWrap.appendChild(img);
    }

    // Name + stats
    document.getElementById('detail-name').textContent = d.name;
    document.getElementById('detail-stats').textContent = d.video_count + ' videos \u00B7 ' + fmtSize(d.total_size);
    document.getElementById('video-count-label').textContent = d.video_count + ' videos';

    // Playlists — quick preview tags + link to full playlists page
    var plHtml = '';
    if (d.playlists && d.playlists.length > 0) {
        var SHOW_LIMIT = 6;
        var sorted = d.playlists.slice().sort(function(a, b) { return b.video_count - a.video_count; });
        var makeTag = function(pl) {
            return '<a class="pl-tag" href="/library/' + encodeURIComponent(d.name) + '/playlist/' + encodeURIComponent(pl.id) + '?sort=playlist_index">'
                + escHtml(pl.title) + ' <span class="pl-count">(' + pl.video_count + ')</span></a>';
        };
        plHtml = '<div class="pl-cloud">';
        plHtml += '<a href="/library/' + encodeURIComponent(d.name) + '/playlists" class="pl-label-link">' + d.playlists.length + ' playlists \u2192</a>';
        sorted.slice(0, SHOW_LIMIT).forEach(function(pl) { plHtml += makeTag(pl); });
        if (sorted.length > SHOW_LIMIT) {
            plHtml += '<a href="/library/' + encodeURIComponent(d.name) + '/playlists" class="pl-more">+ ' + (sorted.length - SHOW_LIMIT) + ' more</a>';
        }
        plHtml += '</div>';
    }
    document.getElementById('detail-playlists').innerHTML = plHtml;

    // Action buttons
    var acts = document.getElementById('detail-actions');
    var btns = '';
    var disabled = _st.jobRunning ? ' disabled' : '';

    if (d.channel_url) {
        btns += '<button class="btn btn-accent btn-sm" onclick="channelAction(\'quick-check\',\'' + escAttr(d.name) + '\',\'' + escAttr(d.channel_url) + '\')"' + disabled + '>Quick Check</button>';
        btns += '<button class="btn btn-blue btn-sm" onclick="channelAction(\'download\',\'' + escAttr(d.name) + '\',\'' + escAttr(d.channel_url) + '\')"' + disabled + '>Download All</button>';
    }
    if (d.on_disk) {
        btns += '<button class="btn btn-ghost btn-sm" onclick="channelAction(\'reindex\',\'' + escAttr(d.name) + '\')">Reindex</button>';
    }
    if (d.channel_url) {
        btns += '<button class="btn btn-ghost btn-sm" onclick="channelAction(\'reindex-playlists\',\'' + escAttr(d.name) + '\')">Reindex Playlists</button>';
    }
    if (d.playlists && d.playlists.length > 0) {
        btns += '<a href="/library/' + encodeURIComponent(d.name) + '/playlists" class="btn btn-ghost btn-sm">View Playlists</a>';
    }
    if (!d.has_poster && d.channel_url) {
        btns += '<button class="btn btn-orange btn-sm" onclick="channelAction(\'fetch-art\',\'' + escAttr(d.name) + '\')">Fetch Art</button>';
    }
    if (d.channel_url) {
        btns += '<a href="' + escAttr(d.channel_url) + '" target="_blank" class="btn btn-ghost btn-sm">YouTube ↗</a>';
    }
    if (d.subscribed) {
        btns += '<button class="btn btn-danger btn-sm" onclick="channelAction(\'unsubscribe\',\'' + escAttr(d.name) + '\',\'' + escAttr(d.channel_url || '') + '\')">Unsubscribe</button>';
    }
    if (d.on_disk) {
        btns += '<button class="btn btn-danger btn-sm" onclick="channelAction(\'delete\',\'' + escAttr(d.name) + '\')">Delete Folder</button>';
    }
    acts.innerHTML = btns;

    // Latest Video card
    var latestWrap = document.getElementById('detail-latest');
    if (latestWrap) {
        var latest = null;
        if (d.videos && d.videos.length > 0) {
            latest = d.videos.slice().sort(function(a, b) { return (b.date || '').localeCompare(a.date || ''); })[0];
        }
        if (latest) {
            var ltThumb = latest.has_thumb
                ? '<div class="latest-thumb" style="background-image:url(\'/media/thumb/' + encodeURIComponent(latest.id) + '\')"><span class="video-duration">' + fmtDuration(latest.duration) + '</span></div>'
                : '<div class="latest-thumb"><div class="video-thumb-ph">▶</div><span class="video-duration">' + fmtDuration(latest.duration) + '</span></div>';
            latestWrap.innerHTML = '<a href="/watch/' + encodeURIComponent(latest.id) + '" class="latest-card">' +
                ltThumb +
                '<div class="latest-info">' +
                '<div class="latest-label">Latest</div>' +
                '<div class="latest-title">' + escHtml(latest.title) + '</div>' +
                '<div class="latest-meta">' + fmtDate(latest.date) + (latest.view_count > 0 ? ' \u00B7 ' + fmtViews(latest.view_count) + ' views' : '') + '</div>' +
                '</div></a>';
            latestWrap.style.display = '';
        } else {
            latestWrap.innerHTML = '';
            latestWrap.style.display = 'none';
        }
    }

    // Render videos
    _st.videos = d.videos;
    _st.page = 1;
    renderVideos(d.videos);
}

/* ═══ Render videos ═══ */
function renderVideos(videos) {
    var total = videos ? videos.length : 0;
    var perPage = _st.perPage;
    var totalPages = Math.max(1, Math.ceil(total / perPage));
    if (_st.page > totalPages) _st.page = totalPages;
    var start = (_st.page - 1) * perPage;
    var end = Math.min(start + perPage, total);
    var pageVids = videos ? videos.slice(start, end) : [];

    renderVideoGrid(pageVids);
    renderVideoList(pageVids);
    renderPagination(total, start, end, totalPages);
}

function renderPagination(total, start, end, totalPages) {
    var container = document.getElementById('pagination-controls');
    if (!container) return;
    if (total <= _st.perPage) {
        container.style.display = 'none';
        return;
    }
    container.style.display = 'flex';
    var html = '<button class="btn btn-sm btn-ghost" id="page-prev"' + (_st.page <= 1 ? ' disabled' : '') + ' onclick="changePage(-1)">Previous</button>';
    html += '<span class="pagination-info">Showing ' + (start + 1) + '-' + end + ' of ' + total + '</span>';
    html += '<button class="btn btn-sm btn-ghost" id="page-next"' + (_st.page >= totalPages ? ' disabled' : '') + ' onclick="changePage(1)">Next</button>';
    container.innerHTML = html;
}

function changePage(delta) {
    _st.page += delta;
    if (_st.page < 1) _st.page = 1;
    renderVideos(_st.videos);
    // Scroll to top of video area
    var toolbar = document.getElementById('video-toolbar');
    if (toolbar) toolbar.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderVideoGrid(videos) {
    var container = document.getElementById('video-grid');
    if (!videos || videos.length === 0) {
        container.innerHTML = '<div class="text-dim text-center" style="grid-column:1/-1;padding:40px 0;">No videos found</div>';
        return;
    }
    var bulkMode = document.getElementById('bulk-mode-toggle') && document.getElementById('bulk-mode-toggle').checked;
    var html = '';
    videos.forEach(function(v) {
        var thumbStyle = v.has_thumb
            ? "background-image:url('/media/thumb/" + encodeURIComponent(v.id) + "')"
            : '';
        var thumbPh = v.has_thumb ? '' : '<div class="video-thumb-ph">🎬</div>';
        var dur = v.duration ? '<span class="video-duration">' + fmtDuration(v.duration) + '</span>' : '';
        var watched = v.watched ? '<span class="video-watched-badge">✓ Watched</span>' : '';
        var progress = '';
        var progressLabel = '';
        if (!v.watched && v.progress > 5 && v.duration > 0) {
            var pct = Math.min((v.progress / v.duration) * 100, 100);
            progress = '<div class="video-progress-bar" style="width:' + pct.toFixed(1) + '%"></div>';
            progressLabel = '<span class="video-progress-label">' + Math.round(pct) + '% watched</span>';
        }
        var watchedClass = v.watched ? ' is-watched' : '';
        var checkbox = bulkMode ? '<input type="checkbox" class="bulk-checkbox" data-id="' + escHtml(v.id) + '" onclick="event.preventDefault();event.stopPropagation();this.checked=!this.checked;updateBulkCount();" style="position:absolute;top:8px;left:8px;z-index:5;width:18px;height:18px;cursor:pointer;">' : '';

        html += '<a class="video-card' + watchedClass + '" style="position:relative;" ' + (bulkMode ? 'onclick="event.preventDefault();var cb=this.querySelector(\'.bulk-checkbox\');cb.checked=!cb.checked;updateBulkCount();"' : 'href="/watch/' + encodeURIComponent(v.id) + '"') + '>' +
            checkbox +
            '<div class="video-thumb" style="' + thumbStyle + '">' + thumbPh + dur + watched + progressLabel + progress + '</div>' +
            '<div class="video-info">' +
            '<div class="video-title">' + escHtml(v.title) + '</div>' +
            '<div class="video-meta">' + fmtDate(v.date) + ' \u00B7 ' + fmtSize(v.size) + (v.view_count > 0 ? ' \u00B7 ' + fmtViews(v.view_count) + ' views' : '') + '</div>' +
            '</div></a>';
    });
    container.innerHTML = html;
}

function renderVideoList(videos) {
    var tbody = document.getElementById('video-list-tbody');
    if (!videos || videos.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-dim text-center" style="padding:40px 0;">No videos found</td></tr>';
        return;
    }
    var html = '';
    videos.forEach(function(v) {
        html += '<tr onclick="window.location.href=\'/watch/' + encodeURIComponent(v.id) + '\'">' +
            '<td class="vl-title"><a href="/watch/' + encodeURIComponent(v.id) + '">' + escHtml(v.title) + '</a></td>' +
            '<td class="text-center nowrap">' + fmtDate(v.date) + '</td>' +
            '<td class="text-center nowrap">' + fmtDuration(v.duration) + '</td>' +
            '<td class="text-right nowrap">' + fmtSize(v.size) + '</td>' +
            '<td class="text-right nowrap">' + (v.view_count > 0 ? fmtViews(v.view_count) : '\u2014') + '</td>' +
            '<td class="text-center">' + (v.watched ? '<span class="badge badge-green">✓</span>' : (v.progress > 5 ? '<span class="badge badge-blue">⏳</span>' : '\u2014')) + '</td>' +
            '</tr>';
    });
    tbody.innerHTML = html;
}

/* ═══ Sort videos ═══ */
function sortVideos() {
    if (!_st.data || !_st.data.videos) return;
    _st.sort = document.getElementById('video-sort').value;
    var vids = _st.data.videos.slice(); // copy

    var comparators = {
        'newest': function(a, b) { return (b.date || '').localeCompare(a.date || ''); },
        'oldest': function(a, b) { return (a.date || '').localeCompare(b.date || ''); },
        'title': function(a, b) { return (a.title || '').toLowerCase().localeCompare((b.title || '').toLowerCase()); },
        'largest': function(a, b) { return (b.size || 0) - (a.size || 0); },
        'smallest': function(a, b) { return (a.size || 0) - (b.size || 0); },
        'longest': function(a, b) { return (b.duration || 0) - (a.duration || 0); },
        'shortest': function(a, b) { return (a.duration || 0) - (b.duration || 0); },
        'most_viewed': function(a, b) { return (b.view_count || 0) - (a.view_count || 0); }
    };

    var cmp = comparators[_st.sort] || comparators['newest'];
    vids.sort(cmp);
    _st.videos = vids;
    _st.page = 1;
    renderVideos(vids);
}

/* ═══ Channel actions ═══ */
function channelAction(action, channelName, channelUrl) {
    var encoded = encodeURIComponent(channelName);

    if (action === 'unsubscribe' && !confirm('Unsubscribe from ' + channelName + '?')) return;
    if (action === 'delete' && !confirm('Delete folder for ' + channelName + '? This removes all files.')) return;

    // Build form and submit for actions that need POST
    var form = document.createElement('form');
    form.method = 'POST';
    form.style.display = 'none';

    switch (action) {
        case 'quick-check':
            form.action = '/run/fast-single';
            addField(form, 'url', channelUrl);
            addField(form, 'folder', channelName);
            break;
        case 'download':
            form.action = '/run/single';
            addField(form, 'url', channelUrl);
            addField(form, 'folder', channelName);
            break;
        case 'reindex':
            form.action = '/api/reindex/' + encoded;
            break;
        case 'reindex-playlists':
            form.action = '/api/reindex-playlists/' + encoded;
            break;
        case 'fetch-art':
            form.action = '/api/fetch-art/' + encoded;
            break;
        case 'unsubscribe':
            form.action = '/remove';
            addField(form, 'url', channelUrl || '');
            addField(form, 'name', channelName);
            break;
        case 'delete':
            form.action = '/api/delete-folder/' + encoded;
            break;
        default:
            return;
    }

    document.body.appendChild(form);
    form.submit();
}

function addField(form, name, value) {
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value || '';
    form.appendChild(input);
}

/* ═══ Util ═══ */
function escHtml(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(s));
    return d.innerHTML;
}

function escAttr(s) {
    return (s || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

/* ═══ Bulk Select / Delete ═══ */
function toggleBulkMode(on) {
    var bar = document.getElementById('bulk-action-bar');
    if (bar) bar.style.display = on ? 'flex' : 'none';
    // Re-render videos to add/remove checkboxes
    if (_st.videos) renderVideos(_st.videos);
    if (!on) updateBulkCount();
}

function updateBulkCount() {
    var checks = document.querySelectorAll('.bulk-checkbox:checked');
    var label = document.getElementById('bulk-count');
    if (label) label.textContent = checks.length + ' selected';
}

function bulkSelectAll() {
    document.querySelectorAll('.bulk-checkbox').forEach(function(cb) { cb.checked = true; });
    updateBulkCount();
}

function bulkSelectNone() {
    document.querySelectorAll('.bulk-checkbox').forEach(function(cb) { cb.checked = false; });
    updateBulkCount();
}

function bulkDeleteSelected() {
    var checks = document.querySelectorAll('.bulk-checkbox:checked');
    if (checks.length === 0) { alert('No videos selected.'); return; }

    var ids = [];
    checks.forEach(function(cb) { ids.push(cb.dataset.id); });

    showDeleteModal(ids);
}

/* ═══ Delete Confirmation Modal ═══ */
function showDeleteModal(videoIds) {
    // Remove old modal if exists
    var old = document.getElementById('delete-modal-overlay');
    if (old) old.remove();

    var count = videoIds.length;
    var overlay = document.createElement('div');
    overlay.id = 'delete-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    var modal = document.createElement('div');
    modal.className = 'modal-dialog';
    modal.innerHTML =
        '<h3 style="margin:0 0 12px;font-size:16px;color:var(--text);">Delete ' + count + ' video' + (count > 1 ? 's' : '') + '?</h3>' +
        '<p style="font-size:13px;color:var(--text-dim);margin:0 0 16px;">This will permanently remove the selected files from disk.</p>' +
        '<label class="delete-exclude-label">' +
        '<input type="checkbox" id="delete-exclude-cb" checked>' +
        '<div>' +
        '<span style="font-size:13px;font-weight:600;color:var(--text);">Also exclude from future downloads</span>' +
        '<div style="font-size:12px;color:var(--text-dimmer,var(--text-dim));margin-top:2px;">Prevents these videos from being re-downloaded. To get them back, you\'d need to unsubscribe and re-subscribe to the channel.</div>' +
        '</div></label>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px;">' +
        '<button class="btn btn-sm btn-ghost" onclick="document.getElementById(\'delete-modal-overlay\').remove()">Cancel</button>' +
        '<button class="btn btn-sm btn-danger" id="confirm-delete-btn">Delete</button>' +
        '</div>';

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Wire up the confirm button
    document.getElementById('confirm-delete-btn').onclick = function() {
        var exclude = document.getElementById('delete-exclude-cb').checked;
        overlay.remove();
        executeDelete(videoIds, exclude);
    };
}

function executeDelete(ids, exclude) {
    var done = 0;
    var errors = 0;
    var total = ids.length;

    // Send all IDs in one request
    fetch('/api/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_ids: ids, exclude: exclude })
    }).then(function(r) {
        return r.json();
    }).then(function(data) {
        if (data.status === 'ok') {
            var msg = 'Deleted ' + (data.deleted || total) + ' video' + ((data.deleted || total) > 1 ? 's' : '') + '.';
            if (exclude) msg += ' Excluded from future downloads.';
            showToast(msg);
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
        }
        // Refresh channel
        if (_st.current) {
            _st.current = null; // force reload
            selectChannel(data._channel || history.state || location.hash.replace('#', ''));
        }
        location.reload();
    }).catch(function(err) {
        alert('Request failed: ' + err);
        location.reload();
    });
}

/* ═══ Add to Playlist (bulk) ═══ */
function bulkAddToPlaylist() {
    var checks = document.querySelectorAll('.bulk-checkbox:checked');
    if (checks.length === 0) { alert('No videos selected.'); return; }
    showPlaylistModal();
}

function showPlaylistModal() {
    // Remove old modal if exists
    var old = document.getElementById('playlist-modal-overlay');
    if (old) old.remove();

    var overlay = document.createElement('div');
    overlay.id = 'playlist-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    var modal = document.createElement('div');
    modal.className = 'modal-dialog';
    modal.innerHTML = '<h3 style="margin:0 0 12px;font-size:16px;">Add to Playlist</h3>' +
        '<input type="text" id="playlist-name-input" class="form-input" placeholder="Playlist name" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:14px;margin-bottom:12px;">' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;">' +
        '<button class="btn btn-sm btn-ghost" onclick="document.getElementById(\'playlist-modal-overlay\').remove()">Cancel</button>' +
        '<button class="btn btn-sm btn-accent" onclick="submitPlaylistAdd()">Add</button>' +
        '</div>';
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    setTimeout(function() { document.getElementById('playlist-name-input').focus(); }, 50);
}

function submitPlaylistAdd() {
    var nameInput = document.getElementById('playlist-name-input');
    var name = nameInput ? nameInput.value.trim() : '';
    if (!name) { alert('Please enter a playlist name.'); return; }

    var checks = document.querySelectorAll('.bulk-checkbox:checked');
    var ids = [];
    checks.forEach(function(cb) { ids.push(cb.dataset.id); });

    fetch('/api/create-playlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_ids: ids, playlist_name: name, channel_name: _st.current || '' })
    }).then(function(r) {
        if (r.ok) {
            showToast('Added ' + ids.length + ' video' + (ids.length > 1 ? 's' : '') + ' to "' + name + '"');
            var overlay = document.getElementById('playlist-modal-overlay');
            if (overlay) overlay.remove();
        } else {
            r.text().then(function(t) { alert('Error: ' + t); });
        }
    }).catch(function(err) { alert('Error: ' + err); });
}

/* ═══════════════════════════════════════════
   Mix Mode — select multiple channels
   ═══════════════════════════════════════════ */
var _mixMode = false;
var _mixSelected = []; // array of channel names

function toggleMixMode() {
    _mixMode = !_mixMode;
    var btn = document.getElementById('mix-mode-btn');
    btn.classList.toggle('active', _mixMode);

    var grid = document.getElementById('channels-grid-view');
    if (grid) grid.classList.toggle('mix-mode-active', _mixMode);

    if (_mixMode) {
        // Restore previously saved mix channels
        var saved = SlugQueue.getMixChannels();
        _mixSelected = saved.slice();
        injectMixCheckboxes();
        updateMixBar();
    } else {
        removeMixCheckboxes();
        document.getElementById('mix-bar').style.display = 'none';
        // Remove selected styling
        document.querySelectorAll('.channel-grid-card.mix-selected').forEach(function(c) { c.classList.remove('mix-selected'); });
    }
}

function injectMixCheckboxes() {
    document.querySelectorAll('.channel-grid-card').forEach(function(card) {
        // Remove old if present
        var old = card.querySelector('.mix-checkbox-wrap');
        if (old) old.remove();

        var name = card.getAttribute('data-name');
        var wrap = document.createElement('div');
        wrap.className = 'mix-checkbox-wrap';
        if (_mixSelected.indexOf(name) >= 0) {
            wrap.classList.add('checked');
            card.classList.add('mix-selected');
        }
        wrap.onclick = function(e) {
            e.stopPropagation();
            toggleMixChannel(name, wrap, card);
        };
        card.appendChild(wrap);
    });
}

function removeMixCheckboxes() {
    document.querySelectorAll('.mix-checkbox-wrap').forEach(function(el) { el.remove(); });
}

function toggleMixChannel(name, wrap, card) {
    var idx = _mixSelected.indexOf(name);
    if (idx >= 0) {
        _mixSelected.splice(idx, 1);
        wrap.classList.remove('checked');
        card.classList.remove('mix-selected');
    } else {
        _mixSelected.push(name);
        wrap.classList.add('checked');
        card.classList.add('mix-selected');
    }
    SlugQueue.setMixChannels(_mixSelected);
    updateMixBar();
}

function updateMixBar() {
    var bar = document.getElementById('mix-bar');
    var text = document.getElementById('mix-bar-text');
    if (_mixSelected.length > 0) {
        bar.style.display = 'flex';
        text.textContent = _mixSelected.length + ' channel' + (_mixSelected.length > 1 ? 's' : '') + ' selected';
    } else {
        bar.style.display = 'none';
    }
}

function clearMixSelection() {
    _mixSelected = [];
    SlugQueue.clearMix();
    document.querySelectorAll('.mix-checkbox-wrap.checked').forEach(function(w) { w.classList.remove('checked'); });
    document.querySelectorAll('.channel-grid-card.mix-selected').forEach(function(c) { c.classList.remove('mix-selected'); });
    updateMixBar();
}

function shufflePlayMix() {
    if (_mixSelected.length === 0) return;
    // Save mix channels, then fetch a random video
    SlugQueue.setMixChannels(_mixSelected);
    var channelsParam = _mixSelected.map(function(c) { return encodeURIComponent(c); }).join(',');
    fetch('/api/random-video?channels=' + channelsParam)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.id) {
                window.location.href = '/watch/' + encodeURIComponent(data.id);
            } else {
                showToastGlobal('No videos found in selected channels');
            }
        })
        .catch(function() { showToastGlobal('Error fetching random video'); });
}

/* ═══════════════════════════════════════════
   Add to Queue (+) button on video cards
   ═══════════════════════════════════════════ */
function addQueueBtnToVideoCards() {
    // Called after renderVideoGrid to inject + buttons
    document.querySelectorAll('#video-grid .video-card').forEach(function(card) {
        // Skip if already has a queue button
        if (card.querySelector('.queue-add-btn')) return;

        var thumbEl = card.querySelector('.video-thumb');
        if (!thumbEl) return;

        // Extract video data from the card
        var href = card.getAttribute('href') || '';
        var match = href.match(/\/watch\/(.+)/);
        if (!match) return;
        var videoId = decodeURIComponent(match[1]);

        // Find video data from _st.data
        var vData = null;
        if (_st.data && _st.data.videos) {
            for (var i = 0; i < _st.data.videos.length; i++) {
                if (_st.data.videos[i].id === videoId) { vData = _st.data.videos[i]; break; }
            }
        }
        if (!vData) return;

        var btn = document.createElement('div');
        btn.className = 'queue-add-btn';
        btn.textContent = '+';
        btn.title = 'Add to queue';
        btn.onclick = function(e) {
            e.preventDefault();
            e.stopPropagation();
            var item = {
                id: vData.id,
                title: vData.title,
                channel: _st.current,
                duration: vData.duration || 0,
                thumb_url: vData.has_thumb ? '/media/thumb/' + encodeURIComponent(vData.id) : ''
            };
            var added = SlugQueue.add(item);
            showToastGlobal(added ? 'Added to queue' : 'Already in queue');
        };
        thumbEl.appendChild(btn);
    });
}

// Monkey-patch renderVideoGrid to add queue buttons after rendering
var _origRenderVideoGrid = renderVideoGrid;
renderVideoGrid = function(videos) {
    _origRenderVideoGrid(videos);
    // Inject queue add buttons after a tick (DOM needs to update)
    setTimeout(addQueueBtnToVideoCards, 0);
};
