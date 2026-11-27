"""Builds a vertical Short from a script: Pexels/Pixabay B-roll + synced captions + editorial motion graphics."""
import array
import asyncio
import inspect
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

LANG = (os.environ.get("VIDEO_LANG") or "en").lower()      # "en", "hi" (Hindi) or "hinglish"
DEFAULT_VOICES = {
    "en": "en-IN-NeerjaNeural",
    "hi": "hi-IN-SwaraNeural",
    "hinglish": "hi-IN-MadhurNeural",   # native Hindi male; or hi-IN-SwaraNeural (female)
}
VOICE = os.environ.get("TTS_VOICE") or DEFAULT_VOICES.get(LANG, "hi-IN-MadhurNeural")
TTS_RATE = os.environ.get("TTS_RATE") or "+8%"
TTS_PITCH = os.environ.get("TTS_PITCH") or "+0Hz"
VIDEO_STYLE = (os.environ.get("VIDEO_STYLE") or "editorial").lower()  # "editorial" or "full"
MEDIA_MODE = (os.environ.get("MEDIA_MODE") or "photos").lower()       # "photos", "videos" or "hybrid"
FONT = os.environ.get("VIDEO_FONT") or ("Noto Sans Devanagari" if LANG == "hi" else "Noto Sans")

W, H = 1080, 1920
CARD_X, CARD_Y, CARD_W, CARD_H, CARD_R = 64, 340, 952, 1030, 34
CAPTION_Y, CAPTION_H = 1405, 230
PAD = 0.08                 # tight breath pause after each scene (seconds)
SUBCLIP_MIN_DUR = 4.2      # scenes longer than this split into 2 distinct B-roll cuts
XFADE_DUR = 0.22           # cross-transition between sub-clips
WORK = "work"
UA = {"User-Agent": "Mozilla/5.0"}

# Editorial palette (warm notebook aesthetic)
PAPER_BG = (245, 241, 230, 255)
DOT_COLOR = (220, 212, 194, 255)
INK = (28, 25, 23, 255)
MUTED_INK = (96, 90, 82, 255)
ACCENT = (200, 62, 43, 255)
ACCENT_SOFT = (252, 236, 232, 255)
GREEN = (22, 128, 61, 255)
PILL_BG = (252, 250, 245, 255)
PILL_BORDER = (214, 206, 191, 255)


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


# ---------------- fonts & text helpers ----------------

_FONT_CACHE = {}


def find_font_path(bold=True, devanagari=False):
    candidates = []
    if devanagari:
        candidates += [
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
            "C:/Windows/Fonts/NirmalaB.ttf" if bold else "C:/Windows/Fonts/Nirmala.ttf",
        ]
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/seguisb.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def get_font(size, bold=True, devanagari=False):
    key = (size, bold, devanagari)
    if key not in _FONT_CACHE:
        path = find_font_path(bold=bold, devanagari=devanagari)
        if path:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        else:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def wrap_text(draw, text, font, max_width, max_lines=2):
    words = text.split()
    lines, cur = [], []
    for w in words:
        test = " ".join(cur + [w])
        if draw.textlength(test, font=font) <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        lines[-1] = lines[-1].rstrip(".,!?") + "..."
    return lines


def extract_stat(text):
    """Extract a punchy number/metric badge from narration if present."""
    m = re.search(
        r"\b(\d+(?:[.,]\d+)?\s*(?:%|percent|meter|meters|km|kg|floors?|guna|x|minute|minutes|min|ghante|hours?|days?|saal|years?|crore|lakh|million|billion|islands?|pulon|seconds?|sec))\b",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    return None


def _short_clause(text, max_words=5):
    clause = re.split(r"[.,?!;]", text.strip())[0]
    words = clause.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def scene_heading(script, idx, total, scene):
    if scene.get("heading"):
        return scene["heading"]
    if idx == 0:
        return script.get("hook") or script.get("title", "")
    stat = scene.get("stat") or extract_stat(scene["text"])
    if stat:
        return stat
    return _short_clause(scene["text"])


# ---------------- voice & word boundaries ----------------

async def _edge_tts_stream(text, out_mp3):
    import edge_tts

    kwargs = {"rate": TTS_RATE, "pitch": TTS_PITCH}
    if "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters:
        kwargs["boundary"] = "WordBoundary"
    comm = edge_tts.Communicate(text, VOICE, **kwargs)
    audio = bytearray()
    words = []
    async for chunk in comm.stream():
        ctype = chunk.get("type")
        if ctype == "audio":
            audio.extend(chunk["data"])
        elif ctype == "WordBoundary":
            w_text = (chunk.get("text") or "").strip()
            if w_text:
                words.append({
                    "text": w_text,
                    "start": chunk["offset"] / 1e7,
                    "dur": max(chunk.get("duration", 0) / 1e7, 0.08),
                })
    with open(out_mp3, "wb") as f:
        f.write(audio)
    return words


def _trim_silence_wav(in_wav, out_wav, pad_sec=PAD, threshold=420):
    """Trim leading/trailing silence in PCM16 mono WAV; return (trim_start_s, speech_dur_s, total_dur_s)."""
    with wave.open(in_wav, "rb") as wf:
        rate = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sw != 2 or nch != 1 or not raw:
        shutil.copyfile(in_wav, out_wav)
        d = probe(out_wav)
        return 0.0, max(d - pad_sec, 0.2), d

    samples = array.array("h")
    samples.frombytes(raw)
    n = len(samples)

    first = next((i for i, s in enumerate(samples) if abs(s) > threshold), 0)
    last = next((i for i in range(n - 1, -1, -1) if abs(samples[i]) > threshold), n - 1)

    pre = int(0.03 * rate)
    post = int(0.04 * rate)
    i0 = max(0, first - pre)
    i1 = min(n, last + post)
    if i1 <= i0:
        i0, i1 = 0, n

    trimmed = samples[i0:i1]
    pad_frames = int(pad_sec * rate)
    silence = array.array("h", [0] * pad_frames)

    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(trimmed.tobytes() + silence.tobytes())

    trim_start = i0 / rate
    speech_dur = len(trimmed) / rate
    total_dur = (len(trimmed) + pad_frames) / rate
    return trim_start, max(speech_dur, 0.25), total_dur


def _fallback_word_timings(text, speech_dur):
    tokens = text.split()
    if not tokens:
        return []
    weights = [max(len(t), 2) for t in tokens]
    total_w = sum(weights)
    out, t = [], 0.0
    for tok, w in zip(tokens, weights):
        d = speech_dur * w / total_w
        out.append({"text": tok, "start": t, "dur": d})
        t += d
    return out


def _to_devanagari_scenes(texts):
    """Optional: convert Roman Hinglish lines to Devanagari for natural Hindi TTS pronunciation."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if LANG != "hinglish" or not key or not texts:
        return texts
    prompt = (
        "Convert each Hinglish line below into natural Devanagari Hindi script for a Hindi voiceover narrator. "
        "Keep English brand names/places readable in Devanagari or standard spelling and keep numbers as digits. "
        "Reply ONLY as a JSON array of strings of the exact same length.\n"
        + json.dumps(texts, ensure_ascii=False)
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    for model in ("gemini-2.5-flash-lite", "gemini-2.5-flash"):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
            )
            with http_open(req, timeout=45, tries=2) as r:
                data = json.load(r)
            parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            raw = "\n".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
            out = json.loads(raw)
            if isinstance(out, list) and len(out) == len(texts) and all(isinstance(x, str) and x.strip() for x in out):
                print("Hinglish TTS converted to Devanagari via", model)
                return out
        except Exception as e:
            print(f"Devanagari TTS conversion skipped ({model}): {e}")
    return texts


def _map_timings_to_display(display_text, spoken_words, speech_dur):
    """Align spoken WordBoundary timings onto Roman Hinglish caption tokens."""
    tokens = display_text.split()
    if not tokens:
        return []
    if not spoken_words:
        return _fallback_word_timings(display_text, speech_dur)
    if len(tokens) == len(spoken_words):
        return [
            {"text": tok, "start": sw["start"], "dur": sw["dur"]}
            for tok, sw in zip(tokens, spoken_words)
        ]
    t0 = spoken_words[0]["start"]
    t1 = max(spoken_words[-1]["start"] + spoken_words[-1]["dur"], speech_dur)
    span = max(t1 - t0, 0.25)
    weights = [max(len(tok), 2) for tok in tokens]
    total_w = sum(weights)
    mapped, cur = [], t0
    for tok, w in zip(tokens, weights):
        d = span * w / total_w
        mapped.append({"text": tok, "start": cur, "dur": d})
        cur += d
    return mapped


def tts_scene(caption_text, out_wav, work_dir, idx, spoken_text=None):
    """Synthesize speech, trim dead air, and return (total_dur, speech_dur, word_timings)."""
    speak = spoken_text or caption_text
    raw_mp3 = f"{work_dir}/raw_s{idx}.mp3"
    tmp_wav = f"{work_dir}/tmp_s{idx}.wav"
    words = []
    used_edge = False

    try:
        words = asyncio.run(_edge_tts_stream(speak, raw_mp3))
        if os.path.exists(raw_mp3) and os.path.getsize(raw_mp3) > 1000:
            used_edge = True
    except Exception as e:
        print("edge-tts failed, trying gTTS:", e)

    if not used_edge:
        from gtts import gTTS
        is_hi = LANG == "hi" or any("\u0900" <= ch <= "\u097f" for ch in speak)
        gTTS(speak, lang="hi" if is_hi else "en", tld="co.in").save(raw_mp3)
        words = []

    run(["ffmpeg", "-y", "-i", raw_mp3, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", tmp_wav])
    trim_start, speech_dur, total_dur = _trim_silence_wav(tmp_wav, out_wav, pad_sec=PAD)

    for p in (raw_mp3, tmp_wav):
        if os.path.exists(p):
            os.remove(p)

    if words:
        aligned = []
        for w in words:
            st = max(0.0, w["start"] - trim_start)
            if st < speech_dur:
                aligned.append({
                    "text": w["text"],
                    "start": st,
                    "dur": min(w["dur"], max(speech_dur - st, 0.05)),
                })
        words = aligned

    words = _map_timings_to_display(caption_text, words, speech_dur)
    return total_dur, speech_dur, words


# ---------------- footage (Pexels + Pixabay fallback) ----------------

def pexels_search(query, key, portrait=True):
    params = {"query": query, "per_page": 15}
    if portrait:
        params["orientation"] = "portrait"
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": key, **UA})
    with http_open(req) as r:
        return json.load(r).get("videos", [])


def best_pexels_file(video):
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


def _pexels_candidates(query, key, used, count):
    picked = []
    for q, portrait in ((query, True), (query, False), ("nature", True)):
        try:
            vids = pexels_search(q, key, portrait)
        except Exception as e:
            print(f"Pexels search failed for '{q}': {e}")
            continue
        fresh = [v for v in vids if ("px", v["id"]) not in used and best_pexels_file(v)]
        pool = fresh or [v for v in vids if best_pexels_file(v)]
        top = pool[:10]
        random.shuffle(top)
        pool = top + pool[10:]
        for v in pool:
            if len(picked) >= count:
                return picked
            uid = ("px", v["id"])
            if uid in used and fresh:
                continue
            used.add(uid)
            picked.append((best_pexels_file(v), (v.get("user") or {}).get("name", "")))
        if len(picked) >= count:
            return picked
    return picked


def pixabay_search(query, key):
    params = {"key": key, "q": query[:100], "per_page": 20, "safesearch": "true"}
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with http_open(req) as r:
        return json.load(r).get("hits", [])


def pixabay_file(hit):
    vids = hit.get("videos") or {}
    for size in ("large", "medium", "small"):
        f = vids.get(size) or {}
        if f.get("url") and f.get("width"):
            return f["url"], f["width"], f["height"]
    return None


def _pixabay_candidates(query, key, used, count):
    words = query.split()
    attempts = [query, " ".join(words[:2]), words[0] if words else "", "nature"]
    seen, picked = set(), []
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
        fresh = [h for h in hits if ("pb", h["id"]) not in used] or hits
        portrait = [h for h in fresh if pixabay_file(h)[2] > pixabay_file(h)[1]]
        pool = portrait or fresh
        top = pool[:10]
        random.shuffle(top)
        pool = top + pool[10:]
        for h in pool:
            if len(picked) >= count:
                return picked
            uid = ("pb", h["id"])
            if uid in used and any(("pb", x["id"]) not in used for x in pool):
                continue
            used.add(uid)
            picked.append((pixabay_file(h)[0], h.get("user", "")))
        if len(picked) >= count:
            return picked
    return picked


def provider_name():
    parts = []
    if os.environ.get("PIXABAY_API_KEY"):
        parts.append("Pixabay")
    if os.environ.get("PEXELS_API_KEY"):
        parts.append("Pexels")
    if MEDIA_MODE in ("photos", "hybrid"):
        parts.append("Wikimedia Commons")
    return ", ".join(parts) or "Stock Media"


def _pexels_photo_candidates(query, key, used, count):
    picked = []
    params = {"query": query, "per_page": 15}
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Authorization": key, **UA})
        with http_open(req) as r:
            photos = json.load(r).get("photos", [])
    except Exception as e:
        print(f"Pexels photo search failed for '{query}': {e}")
        return []
    fresh = [p for p in photos if ("pxp", p["id"]) not in used and (p.get("src") or {}).get("large2x")]
    pool = fresh or [p for p in photos if (p.get("src") or {}).get("large2x")]
    for p in pool:
        if len(picked) >= count:
            break
        uid = ("pxp", p["id"])
        if uid in used and fresh:
            continue
        used.add(uid)
        src = p["src"]
        img_url = src.get("large2x") or src.get("large") or src.get("original")
        if img_url:
            picked.append((img_url, p.get("photographer", ""), "photo"))
    return picked


def _pixabay_photo_candidates(query, key, used, count):
    params = {
        "key": key,
        "q": query[:100],
        "image_type": "photo",
        "order": "popular",
        "per_page": 20,
        "safesearch": "true",
    }
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=UA)
        with http_open(req) as r:
            hits = json.load(r).get("hits", [])
    except Exception as e:
        print(f"Pixabay photo search failed for '{query}': {e}")
        return []
    hits = [
        h for h in hits
        if (h.get("largeImageURL") or h.get("webformatURL"))
        and max(h.get("imageWidth") or 0, h.get("imageHeight") or 0) >= 700
    ]
    fresh = [h for h in hits if ("pbp", h["id"]) not in used] or hits
    picked = []
    for h in fresh[:count]:
        used.add(("pbp", h["id"]))
        picked.append((h.get("largeImageURL") or h.get("webformatURL"), h.get("user", ""), "photo"))
    return picked


def _wikimedia_photo_candidates(query, used, count):
    """Free real-world landmark/temple/car/celebrity JPEG photos via Wikimedia Commons."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": query,
        "gsrlimit": 15,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1280,
        "format": "json",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DailyShortsBot/1.0 (educational)"})
        with http_open(req) as r:
            pages = (json.load(r).get("query") or {}).get("pages", {})
    except Exception as e:
        print(f"Wikimedia photo search failed for '{query}': {e}")
        return []

    skip_words = ("map", "logo", "icon", "flag", "diagram", "chart", "coat_of_arms", "emblem")
    picked = []
    for pid, page in pages.items():
        if len(picked) >= count:
            break
        if ("wm", pid) in used:
            continue
        title_l = (page.get("title") or "").lower()
        if any(sw in title_l for sw in skip_words):
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = (info.get("mime") or "").lower()
        w, h = info.get("width", 0), info.get("height", 0)
        if mime != "image/jpeg" or max(w, h) < 700 or min(w, h) < 450:
            continue
        if w > h * 2.4 or h > w * 2.8:
            continue
        img_url = info.get("thumburl") or info.get("url")
        if not img_url:
            continue
        used.add(("wm", pid))
        meta = info.get("extmetadata") or {}
        artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()[:40]
        picked.append((img_url, artist or "Wikimedia Commons", "photo"))
    return picked


def _normalize_photo(raw_path, clean_jpg):
    """Fix EXIF rotation and convert any downloaded image into a valid RGB JPEG."""
    with Image.open(raw_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.save(clean_jpg, "JPEG", quality=92)


def get_media_for_scene(query, used, out_prefix, count=2):
    """Fetch up to `count` real stock photos (and/or videos per MEDIA_MODE). Returns ([(path, kind)], credits)."""
    px_key = (os.environ.get("PEXELS_API_KEY") or "").strip()
    pb_key = (os.environ.get("PIXABAY_API_KEY") or "").strip()

    candidates = []
    words = query.split()
    queries = []
    for q in (query, " ".join(words[:2]), words[0] if words else "", "world city"):
        if q and q not in queries:
            queries.append(q)

    if MEDIA_MODE in ("photos", "hybrid"):
        for q in queries:
            if len(candidates) >= count:
                break
            if pb_key:
                candidates.extend(_pixabay_photo_candidates(q, pb_key, used, count - len(candidates)))
            if len(candidates) < count and px_key:
                candidates.extend(_pexels_photo_candidates(q, px_key, used, count - len(candidates)))
            if len(candidates) < count:
                candidates.extend(_wikimedia_photo_candidates(q, used, count - len(candidates)))

    if (MEDIA_MODE == "videos" or (MEDIA_MODE == "hybrid" and len(candidates) < count)) and (px_key or pb_key):
        need = count if MEDIA_MODE == "videos" else count - len(candidates)
        v_cands = []
        if px_key:
            v_cands.extend((u, w, "video") for u, w in _pexels_candidates(query, px_key, used, need))
        if len(v_cands) < need and pb_key:
            v_cands.extend((u, w, "video") for u, w in _pixabay_candidates(query, pb_key, used, need - len(v_cands)))
        if MEDIA_MODE == "hybrid" and v_cands and candidates:
            candidates = [candidates[0], v_cands[0]]
        else:
            candidates.extend(v_cands)

    if not candidates:
        raise RuntimeError(f"No stock photos or videos found for '{query}'")

    media_items, credits = [], []
    for j, (url, who, kind) in enumerate(candidates[:count]):
        if kind == "photo":
            raw_p = f"{out_prefix}_{j}_raw"
            jpg_p = f"{out_prefix}_{j}.jpg"
            try:
                download(url, raw_p)
                _normalize_photo(raw_p, jpg_p)
                media_items.append((jpg_p, "photo"))
                if who and who not in credits:
                    credits.append(who)
            except Exception as e:
                print(f"Photo download/decode failed ({url}): {e}")
            finally:
                if os.path.exists(raw_p):
                    os.remove(raw_p)
        else:
            vid_p = f"{out_prefix}_{j}.mp4"
            download(url, vid_p)
            media_items.append((vid_p, "video"))
            if who and who not in credits:
                credits.append(who)

    if not media_items:
        raise RuntimeError(f"All downloaded media failed for '{query}'")
    return media_items, credits


# ---------------- captions & overlays ----------------

def group_word_chunks(words, speech_dur, max_words=3, max_chars=20):
    """Group word timestamps into tight 2-3 word chunks with continuous timing."""
    if not words:
        return []
    raw_chunks, cur = [], []
    for w in words:
        cur_text = " ".join(x["text"] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(cur_text) > max_chars):
            raw_chunks.append(cur)
            cur = [w]
        else:
            cur.append(w)
            if w["text"].endswith((",", ".", "?", "!", ";")):
                raw_chunks.append(cur)
                cur = []
    if cur:
        raw_chunks.append(cur)

    chunks = []
    for idx, ch in enumerate(raw_chunks):
        t0 = ch[0]["start"]
        if idx + 1 < len(raw_chunks):
            t1 = raw_chunks[idx + 1][0]["start"]
        else:
            t1 = max(ch[-1]["start"] + ch[-1]["dur"], speech_dur)
        t1 = max(t1, t0 + 0.14)
        chunks.append((t0, t1, " ".join(x["text"] for x in ch)))
    return chunks


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_ass(all_chunks, path):
    """Clean ASS subtitles (used for Devanagari Hindi where HarfBuzz shaping is essential)."""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{FONT},66,&H0017191C,&H000000FF,&H00F4F8FC,&H40000000,"
        "-1,0,0,0,100,100,0,0,3,18,2,2,70,70,350,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines = []
    for t0, t1, text in all_chunks:
        safe = text.replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Default,,0,0,0,,{safe}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


def _draw_shadow_card(canvas, box, radius, fill, border=None, border_w=2, shadow_offset=(0, 8), shadow_blur=14, shadow_alpha=42):
    """Draw a rounded card with a soft paper drop shadow onto an RGBA canvas."""
    x0, y0, x1, y1 = box
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle(
        [x0 + shadow_offset[0], y0 + shadow_offset[1], x1 + shadow_offset[0], y1 + shadow_offset[1]],
        radius=radius,
        fill=(25, 22, 18, shadow_alpha),
    )
    sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
    canvas.alpha_composite(sh)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=border_w if border else 0)


def render_caption_card(text, path, chunk_idx=0):
    """Render a paper-cutout caption pill PNG (1080 x CAPTION_H) with keyword accent."""
    img = Image.new("RGBA", (W, CAPTION_H), (0, 0, 0, 0))
    font = get_font(56, bold=True)
    probe_draw = ImageDraw.Draw(img)

    tokens = text.split()
    space_w = probe_draw.textlength(" ", font=font)
    widths = [probe_draw.textlength(tok, font=font) for tok in tokens]
    total_w = sum(widths) + space_w * max(len(tokens) - 1, 0)

    if total_w > W - 140:
        font = get_font(46, bold=True)
        space_w = probe_draw.textlength(" ", font=font)
        widths = [probe_draw.textlength(tok, font=font) for tok in tokens]
        total_w = sum(widths) + space_w * max(len(tokens) - 1, 0)

    pad_x, pad_y = 42, 24
    bbox = probe_draw.textbbox((0, 0), "Ag", font=font)
    text_h = bbox[3] - bbox[1]
    box_w = min(W - 80, int(total_w + pad_x * 2))
    box_h = int(text_h + pad_y * 2)
    bx0 = (W - box_w) // 2
    by0 = (CAPTION_H - box_h) // 2

    card = Image.new("RGBA", (W, CAPTION_H), (0, 0, 0, 0))
    _draw_shadow_card(
        card,
        [bx0, by0, bx0 + box_w, by0 + box_h],
        radius=20,
        fill=PILL_BG,
        border=PILL_BORDER,
        border_w=2,
        shadow_offset=(0, 7),
        shadow_blur=12,
        shadow_alpha=52,
    )
    cd = ImageDraw.Draw(card)
    x = (W - total_w) / 2
    y = by0 + pad_y - bbox[1] - 2
    for tok, tw in zip(tokens, widths):
        is_num = bool(re.search(r"\d|%", tok))
        color = ACCENT if is_num else INK
        cd.text((x, y), tok, font=font, fill=color)
        x += tw + space_w

    angle = -0.55 if chunk_idx % 2 == 0 else 0.45
    card = card.rotate(angle, resample=Image.Resampling.BICUBIC, center=(W // 2, CAPTION_H // 2))
    img.alpha_composite(card)
    img.save(path)


def _dot_grid_paper():
    """Create the warm cream dot-grid notebook canvas (1080x1920)."""
    img = Image.new("RGBA", (W, H), PAPER_BG)
    draw = ImageDraw.Draw(img)
    step = 40
    for yy in range(28, H, step):
        for xx in range(28, W, step):
            draw.ellipse([xx - 2, yy - 2, xx + 2, yy + 2], fill=DOT_COLOR)
    return img


def render_scene_hud(script, idx, total_scenes, scene, path):
    """Render the scene frame overlay PNG (1080x1920): paper matte + header + stat sticker + closing card."""
    dev = LANG == "hi"
    heading = scene_heading(script, idx, total_scenes, scene)
    stat_badge = scene.get("stat") or extract_stat(scene["text"])

    if VIDEO_STYLE == "editorial":
        canvas = _dot_grid_paper()
        # Drop shadow behind the rounded video viewport
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle(
            [CARD_X, CARD_Y + 10, CARD_X + CARD_W, CARD_Y + CARD_H + 10],
            radius=CARD_R,
            fill=(25, 20, 15, 65),
        )
        sh = sh.filter(ImageFilter.GaussianBlur(18))
        canvas.alpha_composite(sh)

        # Cut out a clean rounded window using alpha mask
        mask = Image.new("L", (W, H), 255)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle([CARD_X, CARD_Y, CARD_X + CARD_W, CARD_Y + CARD_H], radius=CARD_R, fill=0)
        canvas.putalpha(mask)

        # Subtle crisp border around the window
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            [CARD_X - 1, CARD_Y - 1, CARD_X + CARD_W + 1, CARD_Y + CARD_H + 1],
            radius=CARD_R,
            outline=(210, 202, 186, 255),
            width=3,
        )

        # Bold editorial headline above video card (clean, no Point/Hook/Recap tag)
        if not dev and heading:
            h_font = get_font(56 if idx == 0 else 60, bold=True)
            lines = wrap_text(draw, heading, h_font, W - 120, max_lines=2)
            hy = 132 if len(lines) == 2 else 172
            for line in lines:
                lw = draw.textlength(line, font=h_font)
                draw.text(((W - lw) / 2, hy), line, font=h_font, fill=INK)
                hy += 68
    else:
        # Full-bleed HUD: subtle top gradient + clean headline
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        for y_row in range(0, 360):
            alpha = int(175 * (1 - y_row / 360) ** 1.6)
            draw.line([(0, y_row), (W, y_row)], fill=(12, 12, 14, alpha))
        if not dev and heading:
            h_font = get_font(54, bold=True)
            lines = wrap_text(draw, heading, h_font, W - 120, max_lines=2)
            hy = 120
            for line in lines:
                lw = draw.textlength(line, font=h_font)
                draw.text(((W - lw) / 2 + 2, hy + 3), line, font=h_font, fill=(0, 0, 0, 170))
                draw.text(((W - lw) / 2, hy), line, font=h_font, fill=(255, 255, 255, 255))
                hy += 64

    # Stat sticker pill inside bottom-left of video frame
    if not dev and stat_badge and idx > 0 and idx < total_scenes - 1:
        s_font = get_font(38, bold=True)
        sw = int(ImageDraw.Draw(canvas).textlength(stat_badge, font=s_font)) + 52
        sx0, sy0 = CARD_X + 34, CARD_Y + CARD_H - 110
        _draw_shadow_card(
            canvas,
            [sx0, sy0, sx0 + sw, sy0 + 74],
            radius=18,
            fill=(255, 253, 248, 245),
            border=ACCENT,
            border_w=3,
            shadow_offset=(0, 6),
            shadow_blur=10,
            shadow_alpha=70,
        )
        ImageDraw.Draw(canvas).text((sx0 + 26, sy0 + 16), stat_badge, font=s_font, fill=ACCENT)

    # Closing Notebook Checklist Card on the final scene
    if not dev and idx == total_scenes - 1:
        if script.get("takeaways"):
            items = script["takeaways"]
        else:
            derived = []
            for sc_item in script.get("scenes", [])[:-1]:
                val = sc_item.get("stat") or extract_stat(sc_item["text"])
                if val and val not in derived:
                    derived.append(val)
            if len(derived) < 2:
                for sc_item in script.get("scenes", [])[1:-1]:
                    cl = _short_clause(sc_item["text"], max_words=3)
                    if cl and cl not in derived:
                        derived.append(cl)
                    if len(derived) >= 2:
                        break
            items = derived[:2] + ["Follow For More"]
        items = [str(it)[:28] for it in items[:3]]
        c_font = get_font(40, bold=True)
        box_w, box_h = 700, 82 + len(items) * 76
        cx0 = (W - box_w) // 2
        cy0 = CARD_Y + (CARD_H - box_h) // 2
        _draw_shadow_card(
            canvas,
            [cx0, cy0, cx0 + box_w, cy0 + box_h],
            radius=28,
            fill=(252, 250, 245, 242),
            border=PILL_BORDER,
            border_w=3,
            shadow_offset=(0, 12),
            shadow_blur=20,
            shadow_alpha=95,
        )
        d = ImageDraw.Draw(canvas)
        iy = cy0 + 46
        for item in items:
            # Green checkmark box
            d.rounded_rectangle([cx0 + 52, iy + 2, cx0 + 96, iy + 46], radius=8, outline=INK, width=4, fill=(236, 253, 245, 255))
            d.line([(cx0 + 62, iy + 25), (cx0 + 72, iy + 36), (cx0 + 90, iy + 10)], fill=GREEN, width=6)
            d.text((cx0 + 122, iy + 1), item, font=c_font, fill=INK)
            iy += 76

    canvas.save(path)


# ---------------- audio SFX & mastering ----------------

def make_whoosh_wav(path, dur=0.18, rate=44100):
    """Synthesize a soft filtered air/paper whoosh SFX in pure Python."""
    n = int(dur * rate)
    samples = array.array("h")
    rng = random.Random(42)
    lp = 0.0
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 1.6
        noise = rng.uniform(-1.0, 1.0)
        # Smooth sweep low-pass filter
        alpha = 0.08 + 0.25 * math.sin(math.pi * t)
        lp = lp + alpha * (noise - lp)
        val = int(max(-1.0, min(1.0, lp * env * 0.28)) * 32767)
        samples.append(val)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())


# ---------------- scene video assembly ----------------

def _clip_filter(w, h, dur, zoom_in=True, is_photo=False):
    zp_expr = "min(1.0+0.0009*on,1.10)" if zoom_in else "max(1.10-0.0009*on,1.0)"
    frames = max(int(round(dur * 30)) + 4, 15) if is_photo else 1
    pre = f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2}:x='(iw-ow)/2':y='(ih-oh)*0.38'"
    if not is_photo:
        pre += ",fps=30"
    return (
        f"{pre},"
        f"zoompan=z='{zp_expr}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30,"
        f"eq=contrast=1.05:saturation=1.08,setsar=1,format=yuv420p"
    )


def build_scene_video(media_items, dur, hud_png, cap_overlays, out_mp4, idx):
    """Assemble motion photos/clips (1 or 2 sub-cuts) + HUD matte + caption cards for one scene."""
    vw, vh = (CARD_W, CARD_H) if VIDEO_STYLE == "editorial" else (W, H)
    vx, vy = (CARD_X, CARD_Y) if VIDEO_STYLE == "editorial" else (0, 0)

    cmd = ["ffmpeg", "-y"]
    if len(media_items) >= 2 and dur >= SUBCLIP_MIN_DUR:
        d1 = dur / 2.0 + XFADE_DUR / 2.0
        d2 = dur - d1 + XFADE_DUR
        (p0, k0), (p1, k1) = media_items[0], media_items[1]
        for p, k, d in ((p0, k0, d1), (p1, k1, d2)):
            if k == "photo":
                cmd += ["-i", p]
            else:
                cmd += ["-stream_loop", "-1", "-t", f"{d:.3f}", "-i", p]
        f_parts = [
            f"[0:v]{_clip_filter(vw, vh, d1, zoom_in=(idx % 2 == 0), is_photo=(k0 == 'photo'))},trim=duration={d1:.3f},setpts=PTS-STARTPTS[c0]",
            f"[1:v]{_clip_filter(vw, vh, d2, zoom_in=(idx % 2 != 0), is_photo=(k1 == 'photo'))},trim=duration={d2:.3f},setpts=PTS-STARTPTS[c1]",
            f"[c0][c1]xfade=transition=smoothleft:duration={XFADE_DUR:.2f}:offset={d1 - XFADE_DUR:.3f}[vid]",
        ]
        next_idx = 2
    else:
        p0, k0 = media_items[0]
        if k0 == "photo":
            cmd += ["-i", p0]
        else:
            cmd += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", p0]
        f_parts = [f"[0:v]{_clip_filter(vw, vh, dur, zoom_in=(idx % 2 == 0), is_photo=(k0 == 'photo'))},trim=duration={dur:.3f},setpts=PTS-STARTPTS[vid]"]
        next_idx = 1

    if VIDEO_STYLE == "editorial":
        f_parts.append(f"color=c=0xF5F1E6:s={W}x{H}:r=30:d={dur:.3f}[canvas]")
        f_parts.append(f"[canvas][vid]overlay={vx}:{vy}:shortest=1[placed]")
    else:
        f_parts.append("[vid]copy[placed]")

    # HUD matte overlay
    cmd += ["-i", hud_png]
    hud_in = next_idx
    next_idx += 1
    f_parts.append(f"[placed][{hud_in}:v]overlay=0:0[with_hud]")

    cur_label = "with_hud"
    for k, (t0, t1, cap_png) in enumerate(cap_overlays):
        cmd += ["-i", cap_png]
        cap_in = next_idx
        next_idx += 1
        out_label = f"cap{k}"
        f_parts.append(
            f"[{cur_label}][{cap_in}:v]overlay=0:{CAPTION_Y}:enable='between(t,{t0:.3f},{t1:.3f})'[{out_label}]"
        )
        cur_label = out_label

    cmd += [
        "-filter_complex", ";".join(f_parts),
        "-map", f"[{cur_label}]",
        "-t", f"{dur:.3f}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", out_mp4,
    ]
    run(cmd)


# ---------------- build ----------------

def build_video(script, out_path):
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)

    used, credits = set(), []
    seg_names, wav_names = [], []
    scene_starts, all_ass_chunks = [], []
    t_cursor = 0.0
    total_scenes = len(script["scenes"])
    use_ass = (LANG == "hi")
    tts_texts = _to_devanagari_scenes([sc["text"] for sc in script["scenes"]])

    for i, sc in enumerate(script["scenes"]):
        wav = f"{WORK}/s{i}.wav"
        hud = f"{WORK}/hud{i}.png"
        seg = f"{WORK}/v{i}.mp4"

        dur, speech_dur, words = tts_scene(sc["text"], wav, WORK, i, spoken_text=tts_texts[i])
        chunks = group_word_chunks(words, speech_dur)

        cap_overlays = []
        if use_ass:
            for t0, t1, txt in chunks:
                all_ass_chunks.append((t_cursor + t0, t_cursor + t1, txt))
        else:
            for k, (t0, t1, txt) in enumerate(chunks):
                cap_png = f"{WORK}/cap_{i}_{k}.png"
                render_caption_card(txt, cap_png, chunk_idx=k)
                cap_overlays.append((t0, t1, cap_png))

        render_scene_hud(script, i, total_scenes, sc, hud)

        clip_count = 2 if dur >= SUBCLIP_MIN_DUR else 1
        media_items, sc_credits = get_media_for_scene(sc["q"], used, f"{WORK}/raw{i}", count=clip_count)
        for who in sc_credits:
            if who not in credits:
                credits.append(who)

        build_scene_video(media_items, dur, hud, cap_overlays, seg, i)

        for rp, _ in media_items:
            if os.path.exists(rp):
                os.remove(rp)

        scene_starts.append(t_cursor)
        t_cursor += dur
        seg_names.append(f"v{i}.mp4")
        wav_names.append(f"s{i}.wav")
        time.sleep(1)

    for name, files in (("videos.txt", seg_names), ("audios.txt", wav_names)):
        with open(f"{WORK}/{name}", "w") as f:
            f.write("".join(f"file '{n}'\n" for n in files))

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{WORK}/videos.txt",
         "-c", "copy", f"{WORK}/video_all.mp4"])
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{WORK}/audios.txt",
         "-c", "copy", f"{WORK}/voice_all.wav"])

    # Mix voice + subtle transition whooshes (+ optional assets/bg_music.mp3) + loudnorm
    whoosh_wav = f"{WORK}/whoosh.wav"
    make_whoosh_wav(whoosh_wav)
    final_audio = f"{WORK}/audio_master.wav"

    bg_music = next(
        (p for p in ("assets/bg_music.mp3", "assets/bg_music.wav") if os.path.exists(p)),
        None,
    )
    a_cmd = ["ffmpeg", "-y", "-i", f"{WORK}/voice_all.wav"]
    a_filters = []
    mix_labels = ["[0:a]"]
    in_idx = 1

    if len(scene_starts) > 1:
        a_cmd += ["-i", whoosh_wav]
        w_idx = in_idx
        in_idx += 1
        splits = [f"[w{j}]" for j in range(1, len(scene_starts))]
        a_filters.append(f"[{w_idx}:a]asplit={len(splits)}{''.join(splits)}")
        for j, t_cut in enumerate(scene_starts[1:], start=1):
            ms = max(int((t_cut - 0.08) * 1000), 0)
            a_filters.append(f"[w{j}]adelay={ms}:all=1,volume=0.22[ws{j}]")
            mix_labels.append(f"[ws{j}]")

    if bg_music:
        a_cmd += ["-stream_loop", "-1", "-i", bg_music]
        m_idx = in_idx
        in_idx += 1
        a_filters.append(f"[{m_idx}:a]atrim=0:{t_cursor:.3f},volume=0.08[bgm]")
        mix_labels.append("[bgm]")

    if len(mix_labels) > 1:
        a_filters.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
    else:
        a_filters.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    a_cmd += ["-filter_complex", ";".join(a_filters), "-map", "[aout]", "-ar", "44100", "-ac", "1", final_audio]
    run(a_cmd)

    # Top progress bar overlay (+ ASS subtitles for Hindi)
    bar_png = f"{WORK}/bar.png"
    Image.new("RGBA", (W, 10), ACCENT).save(bar_png)

    v_filters = [f"[0:v][2:v]overlay=x='-W+W*t/{max(t_cursor, 0.5):.3f}':y=0[vbar]"]
    if use_ass:
        make_ass(all_ass_chunks, f"{WORK}/captions.ass")
        v_filters.append(f"[vbar]ass={WORK}/captions.ass[vout]")
        v_map = "[vout]"
    else:
        v_map = "[vbar]"

    run([
        "ffmpeg", "-y",
        "-i", f"{WORK}/video_all.mp4",
        "-i", final_audio,
        "-i", bar_png,
        "-filter_complex", ";".join(v_filters),
        "-map", v_map, "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", out_path,
    ])

    print(f"Built {out_path}: {probe(out_path):.1f}s, style={VIDEO_STYLE}, footage by {credits}")
    return credits
