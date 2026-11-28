# Daily Shorts & Reels Bot

Set `VIDEO_LANG` in `.github/workflows/daily.yml`: `hinglish`, `hi` (Hindi) or `en` (English).
Optional: `VIDEO_STYLE` (`editorial` or `full`), `MEDIA_MODE` (`photos`, `hybrid`, or `videos`), `TTS_RATE` (default `+8%`), `USE_GEMINI` (`true`/`false`), `ENABLE_REELS_FEED` (`true`/`false`), and `assets/bg_music.mp3`.
Secrets: `CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN`, `PIXABAY_API_KEY` / `PEXELS_API_KEY` (optional `GEMINI_API_KEY`, `MAKE_WEBHOOK_URL`, or `IG_USER_ID` + `IG_ACCESS_TOKEN`).
Zapier Reels feed URL (once workflow runs on a public repo): `https://raw.githubusercontent.com/<YOUR_USER>/<YOUR_REPO>/main/feed.xml`.
