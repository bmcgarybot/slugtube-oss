"""Quick check: do .info.json files contain playlist metadata?"""
import json, glob, sys

channel = sys.argv[1] if len(sys.argv) > 1 else "Animation Domination High Def"
pattern = f"/shows/{channel}/**/*.info.json"
files = glob.glob(pattern, recursive=True)[:5]

if not files:
    print(f"No .info.json files found for: {channel}")
    sys.exit(1)

for f in files:
    try:
        info = json.load(open(f))
        pid = info.get("playlist_id", "NONE")
        ptitle = info.get("playlist_title", "NONE")
        print(f"playlist_id: {pid}")
        print(f"playlist_title: {ptitle}")
        print()
    except Exception as e:
        print(f"Error reading {f}: {e}")
