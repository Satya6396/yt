import os
import re
import sys
import json
import time
import urllib.error
import urllib.request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from make_video import build_video, provider_name

LANG = (os.environ.get("VIDEO_LANG") or "en").lower()   # "en", "hi" or "hinglish"
# Script files are played in order. Override with SCRIPTS_FILE="a.json,b.json" in daily.yml.
SCRIPTS_FILE = (os.environ.get("SCRIPTS_FILE")
                or {"hi": "scripts_hi.json", "hinglish": "scripts_hinglish.json"}.get(LANG, "scripts.json"))
USE_GEMINI = (os.environ.get("USE_GEMINI") or "").strip().lower() in ("1", "true", "yes")
NICHE = os.environ.get("NICHE") or "study tips and productivity tips for students"
PRIVACY = "public"  # "public", "unlisted" or "private"
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.0-flash",
]


def load_state():
    s = {}
    if os.path.exists("state.json"):
        with open("state.json", encoding="utf-8") as f:
            s = json.load(f)
    s.setdefault("script_index", 0)
    s.setdefault("count", 0)
    s.setdefault("recent", [])
    return s


def save_state(state):
    with open("state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def get_youtube():
    creds = Credentials(
        None,
        refresh_token=os.environ["REFRESH_TOKEN"].strip(),
        client_id=os.environ["CLIENT_ID"].strip(),
        client_secret=os.environ["CLIENT_SECRET"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def _discover_gemini_models(key):
    """Fallback: ask the Gemini API which flash models support generateContent."""
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        models = []
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                name = m.get("name", "").removeprefix("models/")
                if "flash" in name and "image" not in name and "tts" not in name and "audio" not in name:
                    models.append(name)
        return models
    except Exception as e:
        print(f"Could not list Gemini models: {e}")
        return []


def _parse_gemini_json(data):
    """Extract and validate script JSON from Gemini response, handling thoughts/fences/lists."""
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"No candidates returned: {data.get('promptFeedback', data)}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = [p["text"] for p in parts if p.get("text") and not p.get("thought")]
    if not texts:
        texts = [p["text"] for p in parts if p.get("text")]
    if not texts:
        raise ValueError(f"Empty text parts in candidate: {candidates[0]}")

    raw = "\n".join(texts).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    out = json.loads(raw)
    if isinstance(out, list) and out:
        out = out[0]
    if not isinstance(out, dict):
        raise ValueError(f"Expected JSON object, got {type(out).__name__}")

    scenes = out.get("scenes") or []
    if not out.get("title") or len(scenes) < 3:
        raise ValueError(f"Missing title or scenes (<3): keys={list(out.keys())}")
    for idx, s in enumerate(scenes):
        if not isinstance(s, dict) or not (s.get("text") or "").strip() or not (s.get("q") or "").strip():
            raise ValueError(f"Scene {idx} missing 'text' or 'q': {s}")
    return out


def gemini_script(recent):
    """Ask Gemini for a new script (used when USE_GEMINI=true or file scripts run out)."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY is missing or empty.")
        return None
    language = {
        "hi": "Hindi (Devanagari script)",
        "hinglish": "Hinglish (Hindi written in English/Roman letters, as people speak in India)",
    }.get(LANG, "English")
    prompt = (
        f"Write a script for a punchy 32-38 second YouTube Short about {NICHE}. "
        f"Write the title, hook, description, takeaways and narration in {language}, "
        "but keep the footage search query \"q\" and numeric \"stat\" badge in English. "
        "Give practical, well-established facts or advice only; no made-up statistics. "
        "Reply as JSON: {"
        "\"title\": \"max 60 chars\", "
        "\"hook\": \"punchy 2-4 word on-screen hook\", "
        "\"description\": \"1-2 sentences\", "
        "\"takeaways\": [\"short 2-4 word bullet\", \"short 2-4 word bullet\", \"Follow for more\"], "
        "\"scenes\": [{\"text\": \"10-13 crisp words of narration\", "
        "\"q\": \"2-4 word visual stock footage search query\", "
        "\"stat\": \"optional 2-3 word key number/fact badge or empty string\"}]} "
        "with exactly 6 scenes: scene 1 must be a scroll-stopping curiosity hook (under 13 words), "
        "scenes 2-5 deliver fast high-value points, and scene 6 ends with a quick recap and call to follow. "
        f"Do not repeat these titles: {recent}."
    )
    schema = {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "hook": {"type": "STRING"},
            "description": {"type": "STRING"},
            "takeaways": {"type": "ARRAY", "items": {"type": "STRING"}},
            "scenes": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {"type": "STRING"},
                        "q": {"type": "STRING"},
                        "stat": {"type": "STRING"},
                    },
                    "required": ["text", "q"],
                },
            },
        },
        "required": ["title", "description", "scenes"],
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0.8,
        },
    }

    tried = set()
    candidates = list(GEMINI_MODELS)
    i = 0
    discovered = False
    while i < len(candidates):
        model = candidates[i]
        i += 1
        if model in tried:
            continue
        tried.add(model)

        for attempt in range(2):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json", "x-goog-api-key": key},
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
                out = _parse_gemini_json(data)
                print("Script generated by", model)
                return out
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:400]
                print(f"Gemini model {model} HTTP {e.code}: {body}")
                if e.code in (429, 500, 502, 503) and attempt == 0:
                    time.sleep(5)
                    continue
                if e.code == 404 and not discovered:
                    discovered = True
                    for m in _discover_gemini_models(key):
                        if m not in tried and m not in candidates:
                            candidates.append(m)
                break
            except Exception as e:
                print(f"Gemini model {model} failed ({type(e).__name__}): {e}")
                break
    return None


def main():
    if "--test-gemini" in sys.argv:
        state = load_state()
        out = gemini_script(state["recent"][-15:])
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if out else 1)

    state = load_state()
    scripts = []
    if not USE_GEMINI and SCRIPTS_FILE.strip().lower() not in ("", "none"):
        for name in SCRIPTS_FILE.split(","):
            name = name.strip()
            if name:
                with open(name, encoding="utf-8") as f:
                    scripts += json.load(f)

    if not USE_GEMINI and state["script_index"] < len(scripts):
        script = scripts[state["script_index"]]
        from_file = True
    else:
        script = gemini_script(state["recent"][-15:])
        from_file = False
        if script is None:
            print("OUT OF SCRIPTS: add more entries to scripts.json "
                  "(or fix GEMINI_API_KEY for automatic scripts).")
            sys.exit(1)

    credits = build_video(script, "clip.mp4")

    title = f"{script['title']} #Shorts"[:100]
    credit_line = (f"Stock footage: {provider_name()}"
                   + (f" - {', '.join(credits)}" if credits else ""))
    hashtags = script.get("hashtags") or ("#shorts #studytips" + (" #hindi" if LANG in ("hi", "hinglish") else ""))
    description = f"{script.get('description', script['title'])}\n\n{credit_line}\n\n{hashtags}"

    yt = get_youtube()
    body = {
        "snippet": {
            "title": title,
            "description": description[:4900],
            "tags": script.get("tags", ["study tips", "productivity", "students", "shorts"]),
            "categoryId": str(script.get("category", "27")),  # 27 Education, 19 Travel, 2 Autos, 24 Entertainment
        },
        "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload("clip.mp4", chunksize=-1, resumable=True)
    res = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    print("Uploaded video id:", res["id"], "| title:", title)

    if from_file:
        state["script_index"] += 1
    state["count"] += 1
    state["recent"] = (state["recent"] + [script["title"]])[-30:]
    save_state(state)


if __name__ == "__main__":
    main()
