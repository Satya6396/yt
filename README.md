# YouTube Daily Clip Bot

Every day, GitHub Actions cuts the next 55-second vertical clip from your
source video, uses the next title from `titles.txt`, and uploads it to YouTube.

## Setup

### 1. Google Cloud (free)
1. Go to https://console.cloud.google.com and create a project.
2. Enable **YouTube Data API v3**.
3. OAuth consent screen: fill basic info, add your Google account as a test
   user, then set publishing status to **In production**
   (otherwise the refresh token expires after 7 days).
4. Credentials -> Create Credentials -> **OAuth client ID** -> *Desktop app*.
5. Download the JSON and save it as `client_secret.json` in this folder
   (it is git-ignored, never upload it).

### 2. Get your tokens (on your PC, once)
```
pip install google-auth-oauthlib
python generate_token.py
```
Log in with the YouTube channel account. Copy the 3 printed values.

### 3. GitHub
1. Create a new repo and upload everything in this folder
   (except `client_secret.json`).
2. Repo -> Settings -> Secrets and variables -> Actions -> New secret:
   `CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN`.
3. Repo -> Settings -> Actions -> General -> Workflow permissions ->
   **Read and write permissions**.

### 4. Add your content
- `sources.txt`: one source per line (video URL, or file path like
  `videos/my_video.mp4`). GitHub allows files up to 100 MB per file.
- `titles.txt`: one title per line, used in order.

### 5. Test
Actions tab -> *Daily upload* -> **Run workflow**. After that it runs daily.

## Change settings
- Time: edit the `cron` line in `.github/workflows/daily.yml` (UTC time).
- Clip length / privacy: top of `main.py`.

## Notes
- Only use content you own or have permission to use.
- New API projects may have uploads locked to private until audited; check your first upload.
- Downloading YouTube URLs from GitHub servers is often blocked by YouTube;
  uploading your own video file into `videos/` is more reliable.
