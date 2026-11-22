import os
import json
import subprocess

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIP_LEN = 55   # clip length in seconds (keep under 60 for Shorts)
STEP = 60       # how far to move forward in the source each day
PRIVACY = "public"  # "public", "unlisted" or "private"


def load_state():
    if os.path.exists("state.json"):
        with open("state.json") as f:
            return json.load(f)
    return {"source": 0, "offset": 0, "count": 0}


def save_state(state):
    with open("state.json", "w") as f:
        json.dump(state, f)


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path]
    )
    return float(out)


def get_youtube():
    creds = Credentials(
        None,
        refresh_token=os.environ["REFRESH_TOKEN"],
        client_id=os.environ["CLIENT_ID"],
        client_secret=os.environ["CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def get_source_file(src):
    """Return a local path for the source (download if it's a URL)."""
    if src.startswith("http"):
        subprocess.run(
            ["yt-dlp", "-f", "mp4/best", "-o", "source.mp4",
             "--force-overwrites", src],
            check=True,
        )
        return "source.mp4"
    if not os.path.exists(src):
        raise FileNotFoundError(f"Source file not found: {src}")
    return src


def main():
    state = load_state()
    sources = read_lines("sources.txt")
    titles = read_lines("titles.txt")

    if state["source"] >= len(sources):
        print("No more sources. Add more lines to sources.txt")
        return

    src_path = get_source_file(sources[state["source"]])
    total = duration(src_path)

    if state["offset"] + CLIP_LEN > total:
        state["source"] += 1
        state["offset"] = 0
        save_state(state)
        print("Source finished. Will use the next source on the next run.")
        return

    # Cut the clip and convert to vertical 9:16 (Shorts format)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(state["offset"]), "-i", src_path,
         "-t", str(CLIP_LEN),
         "-vf", "crop=ih*9/16:ih,scale=1080:1920",
         "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "clip.mp4"],
        check=True,
    )

    n = state["count"]
    base = titles[n] if n < len(titles) else "Daily Clip"
    title = f"{base} #Shorts"[:100]

    yt = get_youtube()
    body = {
        "snippet": {
            "title": title,
            "description": f"{base}\n\n#shorts",
            "tags": ["shorts", "daily"],
            "categoryId": "22",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload("clip.mp4", chunksize=-1, resumable=True)
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=media).execute()
    print("Uploaded video id:", res["id"])

    state["offset"] += STEP
    state["count"] += 1
    save_state(state)


if __name__ == "__main__":
    main()
