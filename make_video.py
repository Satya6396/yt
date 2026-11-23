"""Builds a vertical Short from a script: free Pexels footage + voiceover + captions."""
import os
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LANG = (os.environ.get("VIDEO_LANG") or "en").lower()      # "en", "hi" (Hindi) or "hinglish"
DEFAULT_VOICES = {"en": "en-IN-NeerjaNeural", "hi": "hi-IN-SwaraNeural",
                  "hinglish": "en-IN-NeerjaNeural"}
# other voices: en-IN-PrabhatNeural, hi-IN-MadhurNeural (male)
VOICE = os.environ.get("TTS_VOICE") or DEFAULT_VOICES.get(LANG, "en-IN-NeerjaNeural")
FONT = os.environ.get("VIDEO_FONT") or ("Noto Sans Devanagari" if LANG == "hi" else "DejaVu Sans")
W, H = 1080, 1920
PAD = 0.25          # seconds of silence after each scene
WORK = "work"
UA = {"User-Agent": "Mozilla/5.0"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError(f"command failed: {cmd[0]}")
    return r


def probe(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path])
    return float(out)


def http_open(req, timeout=60, tries=5):
    """urlopen with waiting + retry on rate limits (429) and temporary server errors."""
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and n < tries - 1:
                wait = 15 * (n + 1)
                print(f"HTTP {e.code} from {req.full_url.split('?')[0]}; waiting {wait}s and retrying...")
                time.sleep(wait)
                continue
            raise


# ---------------- voice ----------------

def tts(text, out_mp3):
    """Free text-to-speech. Tries edge-tts first, then gTTS."""
    try:
        run([sys.executable, "-m", "edge_tts", "--voice", VOICE,
             "--text", text, "--write-media", out_mp3])
        if os.path.getsize(out_mp3) > 1000:
            return
    except Exception as e:
        print("edge-tts failed, trying gTTS:", e)
    from gtts import gTTS
    gTTS(text, lang="hi" if LANG == "hi" else "en", tld="co.in").save(out_mp3)


# ---------------- footage (Pexels, free licence) ----------------

def pexels_search(query, key, portrait=True):
    params = {"query": query, "per_page": 15}
    if portrait:
        params["orientation"] = "portrait"
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": key, **UA})
    with http_open(req) as r:
        return json.load(r).get("videos", [])


def best_file(video):
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("width")]
    if not files:
        return None
    files.sort(key=lambda f: abs(f["width"] - 1080))
    return files[0]["link"]


def download(url, path):
    req = urllib.request.Request(url, headers=UA)
    with http_open(req, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def get_clip_pexels(query, key, used, path):
    """Download one clip for the query. Returns the photographer's name."""
    for q, portrait in ((query, True), (query, False), ("nature", True)):
        try:
            vids = pexels_search(q, key, portrait)
        except Exception as e:
            print(f"Pexels search failed for '{q}': {e}")
            continue
        fresh = [v for v in vids if v["id"] not in used and best_file(v)]
        pool = fresh or [v for v in vids if best_file(v)]
        if pool:
            v = random.choice(pool[:8])
            used.add(v["id"])
            download(best_file(v), path)
            return (v.get("user") or {}).get("name", "")
    raise RuntimeError(f"No footage found for '{query}'")


# ---------------- footage (Pixabay, free Content License) ----------------

def pixabay_search(query, key):
    params = {"key": key, "q": query[:100], "per_page": 20, "safesearch": "true"}
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with http_open(req) as r:
        return json.load(r).get("hits", [])


def pixabay_file(hit):
    """Best available file for a hit: (url, width, height) or None."""
    vids = hit.get("videos") or {}
    for size in ("large", "medium", "small"):
        f = vids.get(size) or {}
        if f.get("url") and f.get("width"):
            return f["url"], f["width"], f["height"]
    return None


def get_clip_pixabay(query, key, used, path):
    words = query.split()
    attempts = [query, " ".join(words[:2]), words[0] if words else "", "nature"]
    seen = set()
    for q in attempts:
        if not q or q in seen:
            continue
        seen.add(q)
        try:
            hits = pixabay_search(q, key)
        except Exception as e:
            print(f"Pixabay search failed for '{q}': {e}")
            continue
        hits = [h for h in hits if pixabay_file(h)]
        fresh = [h for h in hits if h["id"] not in used] or hits
        portrait = [h for h in fresh if pixabay_file(h)[2] > pixabay_file(h)[1]]
        pool = portrait or fresh
        if pool:
            h = random.choice(pool[:8])
            used.add(h["id"])
            download(pixabay_file(h)[0], path)
            return h.get("user", "")
    raise RuntimeError(f"No footage found for '{query}'")


def provider_name():
    if os.environ.get("PEXELS_API_KEY"):
        return "Pexels (pexels.com)"
    return "Pixabay (pixabay.com)"


def get_clip(query, key, used, path):
    if os.environ.get("PEXELS_API_KEY"):
        return get_clip_pexels(query, key, used, path)
    return get_clip_pixabay(query, key, used, path)


# ---------------- captions ----------------

def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def split_words(text, n=4):
    words = text.split()
    return [" ".join(words[i:i + n]) for i in range(0, len(words), n)]


def make_ass(scenes_timing, path):
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{FONT},76,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,6,2,2,80,80,520,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines = []
    for t0, speech_dur, text in scenes_timing:
        chunks = split_words(text)
        weights = [len(c) + 1 for c in chunks]
        total = sum(weights)
        t = t0
        for c, w in zip(chunks, weights):
            d = speech_dur * w / total
            safe = c.upper().replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{safe}")
            t += d
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


# ---------------- build ----------------

def build_video(script, out_path):
    key = os.environ.get("PEXELS_API_KEY") or os.environ["PIXABAY_API_KEY"]
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)

    used, credits, timing = set(), [], []
    seg_names, wav_names = [], []
    t_cursor = 0.0

    for i, sc in enumerate(script["scenes"]):
        mp3 = f"{WORK}/s{i}.mp3"
        wav = f"{WORK}/s{i}.wav"
        raw = f"{WORK}/raw{i}.mp4"
        seg = f"{WORK}/v{i}.mp4"

        tts(sc["text"], mp3)
        run(["ffmpeg", "-y", "-i", mp3, "-af", f"apad=pad_dur={PAD}",
             "-ar", "44100", "-ac", "1", wav])
        dur = probe(wav)

        who = get_clip(sc["q"], key, used, raw)
        if who and who not in credits:
            credits.append(who)

        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", raw, "-t", f"{dur:.3f}",
             "-vf", (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},fps=30,setsar=1"),
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", seg])

        timing.append((t_cursor, max(dur - PAD, 0.5), sc["text"]))
        t_cursor += dur
        seg_names.append(f"v{i}.mp4")
        wav_names.append(f"s{i}.wav")
        os.remove(raw)
        time.sleep(2)

    for name, files in (("videos.txt", seg_names), ("audios.txt", wav_names)):
        with open(f"{WORK}/{name}", "w") as f:
            f.write("".join(f"file '{n}'\n" for n in files))

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{WORK}/videos.txt",
         "-c", "copy", f"{WORK}/video_all.mp4"])
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{WORK}/audios.txt",
         "-c", "copy", f"{WORK}/voice_all.wav"])

    make_ass(timing, f"{WORK}/captions.ass")
    run(["ffmpeg", "-y", "-i", f"{WORK}/video_all.mp4", "-i", f"{WORK}/voice_all.wav",
         "-vf", f"ass={WORK}/captions.ass",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", out_path])

    print(f"Built {out_path}: {probe(out_path):.1f}s, footage by {credits}")
    return credits
