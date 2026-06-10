#!/usr/bin/env python3
"""
Archive Audit for SlugTube
Cross-references downloaded.txt against actual files on disk.
Finds phantom entries (in archive but no file) and orphan files (on disk but not in archive).

Usage as endpoint: POST /api/audit-archive?mode=dry-run  (or mode=rebuild)
Usage standalone:  python3 archive_audit.py /shows /config/archive/downloaded.txt
"""

import os
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path


# Same pattern used by sync-archive
YT_ID_PATTERN = re.compile(r'\[([a-zA-Z0-9_-]{8,15})\]\.[a-zA-Z0-9]+$')
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.m4v')


def load_archive(archive_path):
    """Load all video IDs from the archive file."""
    ids = set()
    try:
        with open(archive_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        ids.add(parts[1])
                    else:
                        ids.add(parts[0])
    except FileNotFoundError:
        pass
    return ids


def scan_disk(shows_dir):
    """Scan all video files on disk and extract YouTube IDs from filenames.
    Returns dict mapping video_id -> file_path for traceability."""
    found = {}
    scanned = 0
    for root, dirs, files in os.walk(shows_dir):
        for fname in files:
            if fname.endswith(VIDEO_EXTENSIONS):
                scanned += 1
                match = YT_ID_PATTERN.search(fname)
                if match:
                    vid_id = match.group(1)
                    # Keep first occurrence (shouldn't have dupes but just in case)
                    if vid_id not in found:
                        found[vid_id] = os.path.join(root, fname)
    return found, scanned


def audit(shows_dir, archive_path):
    """Run the audit. Returns a report dict."""
    archive_ids = load_archive(archive_path)
    disk_files, scanned = scan_disk(shows_dir)
    disk_ids = set(disk_files.keys())

    # Phantom: in archive but NOT on disk
    phantom_ids = archive_ids - disk_ids

    # Orphan: on disk but NOT in archive (sync-archive would fix these)
    orphan_ids = disk_ids - archive_ids

    # Matched: in both
    matched_ids = archive_ids & disk_ids

    # Sample some phantoms for the report
    phantom_sample = sorted(phantom_ids)[:50]

    # Sample some orphans with their paths
    orphan_sample = []
    for vid_id in sorted(orphan_ids)[:50]:
        orphan_sample.append({
            "id": vid_id,
            "path": disk_files[vid_id]
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "archive_path": archive_path,
        "shows_dir": shows_dir,
        "files_scanned": scanned,
        "archive_total": len(archive_ids),
        "disk_total": len(disk_ids),
        "matched": len(matched_ids),
        "phantoms": len(phantom_ids),
        "orphans": len(orphan_ids),
        "phantom_sample": phantom_sample,
        "orphan_sample": orphan_sample,
        "summary": (
            f"Archive has {len(archive_ids):,} entries. "
            f"Disk has {len(disk_ids):,} videos with IDs. "
            f"{len(matched_ids):,} match. "
            f"{len(phantom_ids):,} phantom entries (archive only, no file). "
            f"{len(orphan_ids):,} orphan files (on disk, not in archive)."
        )
    }


def rebuild_archive(shows_dir, archive_path, dry_run=True):
    """Rebuild archive from disk only. Backs up old archive first.
    If dry_run=True, only reports what would happen."""
    report = audit(shows_dir, archive_path)

    if dry_run:
        report["action"] = "dry_run"
        report["message"] = (
            f"Would remove {report['phantoms']:,} phantom entries. "
            f"Would keep {report['matched']:,} matched entries. "
            f"Would add {report['orphans']:,} orphan IDs. "
            f"New archive would have {report['matched'] + report['orphans']:,} entries "
            f"(down from {report['archive_total']:,})."
        )
        return report

    # Back up current archive
    backup_path = archive_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if os.path.exists(archive_path):
        shutil.copy2(archive_path, backup_path)

    # Scan disk for all IDs
    disk_files, _ = scan_disk(shows_dir)

    # Write new archive with only IDs that have files on disk
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    with open(archive_path, "w") as f:
        for vid_id in sorted(disk_files.keys()):
            f.write(f"youtube {vid_id}\n")

    new_count = len(disk_files)
    report["action"] = "rebuilt"
    report["backup_path"] = backup_path
    report["new_archive_total"] = new_count
    report["message"] = (
        f"Archive rebuilt. Backed up to {backup_path}. "
        f"Old: {report['archive_total']:,} entries. "
        f"New: {new_count:,} entries. "
        f"Removed {report['phantoms']:,} phantoms. "
        f"Added {report['orphans']:,} orphans."
    )
    return report


# ── Flask endpoint (imported by app.py) ──

def register_audit_routes(app, archive_file, shows_dir="/shows"):
    """Register audit endpoints on the Flask app."""
    from flask import jsonify, request

    @app.route("/api/audit-archive", methods=["POST"])
    def api_audit_archive():
        """Audit archive vs disk. Returns report with phantom/orphan counts."""
        return jsonify(audit(shows_dir, archive_file))

    @app.route("/api/rebuild-archive", methods=["POST"])
    def api_rebuild_archive():
        """Rebuild archive from disk. Use ?confirm=true to execute, otherwise dry-run."""
        dry_run = request.args.get("confirm", "").lower() != "true"
        result = rebuild_archive(shows_dir, archive_file, dry_run=dry_run)
        return jsonify(result)


# ── Standalone CLI ──

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 archive_audit.py /shows /config/archive/downloaded.txt [--rebuild]")
        sys.exit(1)

    shows = sys.argv[1]
    archive = sys.argv[2]
    do_rebuild = "--rebuild" in sys.argv

    print(f"Scanning {shows}...")
    result = audit(shows, archive)

    print()
    print("=" * 60)
    print(" SlugTube Archive Audit")
    print("=" * 60)
    print(f"  Archive entries:    {result['archive_total']:>8,}")
    print(f"  Videos on disk:     {result['disk_total']:>8,}")
    print(f"  Matched:            {result['matched']:>8,}")
    print(f"  Phantoms (no file): {result['phantoms']:>8,}")
    print(f"  Orphans (no entry): {result['orphans']:>8,}")
    print("=" * 60)
    print()

    if result['phantom_sample']:
        print(f"Sample phantom IDs (first 50 of {result['phantoms']:,}):")
        for pid in result['phantom_sample'][:20]:
            print(f"  youtube {pid}")
        if result['phantoms'] > 20:
            print(f"  ... and {result['phantoms'] - 20:,} more")
        print()

    if result['orphan_sample']:
        print(f"Sample orphan files (first 50 of {result['orphans']:,}):")
        for o in result['orphan_sample'][:10]:
            print(f"  {o['id']} -> {o['path']}")
        print()

    if do_rebuild:
        print("Rebuilding archive from disk...")
        rebuild_result = rebuild_archive(shows, archive, dry_run=False)
        print(rebuild_result['message'])
    else:
        dry = rebuild_archive(shows, archive, dry_run=True)
        print(dry['message'])
        print()
        print("To execute: python3 archive_audit.py /shows /config/archive/downloaded.txt --rebuild")
