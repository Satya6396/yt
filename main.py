import os
import glob
import json
import base64
import urllib.request
import subprocess

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIP_LEN = 55       # clip length in seconds (keep under 60 for Shorts)
STEP = 60           # how far to move forward in the video each day
PRIVACY = "public"  # "public", "unlisted" or "private"
EXTS = ("mp4", "mov", "mkv", "webm", "avi")

# Gemini models are tried in this order until one works (free tier).
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]


def load_state():
    s = {}
    if os.path.exists("state.json"):
        with open("state.json") as f:
            s = json.load(f)
    return {
        "file": s.get("file"),
        "offset": s.get("offset", 0),
        "count": s.get("count", 0),
        "done": s.get("done", []),
        "recent": s.get("recent", []),
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
    skip = {"clip.mp4", "source.mp4", "small.mp4"}
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
    if state["file"] and state["file"] in videos:
        return state["file"]
    for v in videos:
        if v not in state["done"]:
            state["file"] = v
            state["offset"] = 0
            return v
    return None


# ---------------- AI title (Gemini, free tier) ----------------

def ask_gemini(model, key, parts):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def ai_title(clip_path, video_name, recent):
    """Return (title, description) from Gemini, or None if anything fails."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("No GEMINI_API_KEY set, skipping AI title.")
        return None

    prompt = (
        "You write titles for YouTube Shorts. Look at this clip and write ONE "
        "catchy, accurate title (max 60 characters, no hashtags, no quotes) and "
        "a short description (max 2 sentences). "
        f"The original file name is: {os.path.basename(video_name)}. "
        f"Do not repeat these recent titles: {recent}. "
        'Reply as JSON: {"title": "...", "description": "..."}'
    )

    parts = [{"text": prompt}]
    try:
        # small low-res copy so it can be sent inline
        subprocess.run(
            ["ffmpeg", "-y", "-i", clip_path, "-vf", "scale=360:-2", "-r", "5",
             "-c:v", "libx264", "-crf", "32", "-c:a", "aac", "-b:a", "32k",
             "small.mp4"],
            check=True, capture_output=True)
        if os.path.getsize("small.mp4") < 14 * 1024 * 1024:
            with open("small.mp4", "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            parts.insert(0, {"inline_data": {"mime_type": "video/mp4", "data": b64}})
    except Exception as e:
        print("Could not prepare video for AI, using file name only:", e)

    for model in GEMINI_MODELS:
        try:
            out = ask_gemini(model, key, parts)
            title = str(out["title"]).strip().strip('"')
            desc = str(out.get("description", "")).strip()
            if title:
                print(f"AI title from {model}: {title}")
                return title, desc
        except Exception as e:
            print(f"Gemini model {model} failed: {e}")
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
        start, length = 0, min(total, 60)
    elif state["offset"] + CLIP_LEN > total:
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
    result = ai_title("clip.mp4", video, state["recent"][-10:])
    if result:
        base, desc = result
    else:
        base = titles[n] if n < len(titles) else "Daily Clip"
        desc = base
    title = f"{base} #Shorts"[:100]

    yt = get_youtube()
    body = {
        "snippet": {
            "title": title,
            "description": f"{desc}\n\n#shorts",
            "tags": ["shorts", "daily"],
            "categoryId": "22",
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload("clip.mp4", chunksize=-1, resumable=True)
    res = yt.videos().insert(part="snippet,status", body=body,
                             media_body=media).execute()
    print("Uploaded video id:", res["id"], "| title:", title)

    state["count"] += 1
    state["recent"] = (state["recent"] + [base])[-20:]
    if total <= CLIP_LEN + 1:
        state["done"].append(video)
        state["file"] = None
        state["offset"] = 0
    else:
        state["offset"] += STEP
    save_state(state)


if __name__ == "__main__":
    main()
