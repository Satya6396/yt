import os
import glob
import json
import subprocess

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIP_LEN = 55       # clip length in seconds (keep under 60 for Shorts)
STEP = 60           # how far to move forward in the video each day
PRIVACY = "public"  # "public", "unlisted" or "private"
EXTS = ("mp4", "mov", "mkv", "webm", "avi")


def load_state():
    if os.path.exists("state.json"):
        with open("state.json") as f:
            s = json.load(f)
    else:
        s = {}
    return {
        "file": s.get("file"),
        "offset": s.get("offset", 0),
        "count": s.get("count", 0),
        "done": s.get("done", []),
    }


def save_state(state):
    with open("state.json", "w") as f:
        json.dump(state, f, indent=1)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def find_videos():
    """All video files in the repo root and in the videos/ folder."""
    found = []
    for folder in (".", "videos"):
        for ext in EXTS:
            found += glob.glob(os.path.join(folder, f"*.{ext}"))
            found += glob.glob(os.path.join(folder, f"*.{ext.upper()}"))
    skip = {"clip.mp4", "source.mp4"}
    found = [os.path.normpath(p) for p in found
             if os.path.basename(p) not in skip]
    return sorted(set(found))


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


def pick_video(state):
    videos = find_videos()
    # keep working on the current video if it still exists
    if state["file"] and state["file"] in videos:
        return state["file"]
    # otherwise take the first video not used yet
    for v in videos:
        if v not in state["done"]:
            state["file"] = v
            state["offset"] = 0
            return v
    return None


def main():
    state = load_state()
    titles = read_lines("titles.txt")

    video = pick_video(state)
    if video is None:
        print("No new videos found. Upload a video (.mp4) to the repo.")
        save_state(state)
        return

    total = duration(video)
    print(f"Using {video} ({total:.0f}s), offset {state['offset']}s")

    if total <= CLIP_LEN + 1:
        # short video: upload the whole thing (max 60s for Shorts)
        start, length = 0, min(total, 60)
    elif state["offset"] + CLIP_LEN > total:
        # this video is finished, move on to the next one
        state["done"].append(video)
        state["file"] = None
        state["offset"] = 0
        save_state(state)
        print("Video finished. Next video will be used on the next run.")
        return
    else:
        start, length = state["offset"], CLIP_LEN

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-i", video,
         "-t", str(length),
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

    state["count"] += 1
    if total <= CLIP_LEN + 1:
        state["done"].append(video)
        state["file"] = None
        state["offset"] = 0
    else:
        state["offset"] += STEP
    save_state(state)


if __name__ == "__main__":
    main()
