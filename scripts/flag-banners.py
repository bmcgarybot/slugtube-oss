#!/usr/bin/env python3
"""Flag channels that have banner images in the database."""
import sqlite3, os

DB = "/config/slugtube-index.db"
conn = sqlite3.connect(DB)

# Make sure column exists
try:
    conn.execute("SELECT has_banner FROM channels LIMIT 1")
except sqlite3.OperationalError:
    conn.execute("ALTER TABLE channels ADD COLUMN has_banner INTEGER DEFAULT 0")
    print("Added has_banner column")

updated = 0
for row in conn.execute("SELECT name, path FROM channels").fetchall():
    name, path = row
    has = os.path.isfile(os.path.join(path, "banner.jpg")) or os.path.isfile(os.path.join(path, "fanart.jpg"))
    if has:
        conn.execute("UPDATE channels SET has_banner=1 WHERE name=?", (name,))
        updated += 1

conn.commit()
conn.close()
print(f"Done. {updated} channels flagged with banners.")
