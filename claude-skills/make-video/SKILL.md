---
name: make-video
description: Produce a finished YouTube-ready video from a Tella recording using the user's video studio (Tella → HyperFrames intro/outro → background music → FFmpeg assembly). Use when the user asks to make, build, edit, finish or export a video, pull a Tella recording, add music to a video, or update their intro/outro. Finds music on Epidemic Sound, and falls back to free music libraries (Pixabay Music, Incompetech, Free Music Archive) when Epidemic Sound can't download.
---

# Make a video

The studio lives at `~/Claude Code/video-studio`. Run every command from there, with Homebrew on the PATH:

```bash
export PATH="/opt/homebrew/bin:$PATH"; cd "~/Claude Code/video-studio"
```

`README.md` there documents setup; `scripts/assemble.sh` does the FFmpeg work. Mixing levels (`MUSIC_VOLUME`, `TARGET_LUFS`) live in `.env`. Never print or share `.env` or any API key.

## 1. Get the recording

- If the user didn't name a file, run `npm run tella:list` (newest first, `id<TAB>title`) and pick the video they mean. Ask if more than one could fit.
- If the Tella MCP is connected and the user asked for edits (remove fillers/silences, zooms, chapters), make those in Tella first.
- `npm run tella:pull -- <videoId>` → `inbox/<videoId>.mp4`. Placeholder IDs like `vid_XXXX` return 404; use a real ID from the list.
- Note the duration: `ffprobe -v error -show_entries format=duration -of csv=p=0 inbox/<id>.mp4`.

## 2. Understand the video, so the music fits it

Before searching for music, work out what the video is about and how it feels:
- Topic and tone: the title, plus the transcript if the Tella MCP can read it. Otherwise ask the user in one line.
- Energy: calm tutorial → lo-fi/acoustic, 80–100 BPM; product/tech → light corporate/electronic, 100–120 BPM; vlog/lifestyle → upbeat indie pop or acoustic, 100–125 BPM; motivational → building cinematic/pop.
- Always prefer **no vocals** under speech, and a track at least as long as the video (shorter tracks get looped).
- If the user gave a mood ("upbeat", "chill"), that overrides your guess.

## 3. Find music

**Always present 3 options** (title, artist, feel, BPM, length, license, preview link), give your pick, and **wait for the user to choose before downloading anything.**

### A. Epidemic Sound first (if its MCP tools are available)
Search with `SearchRecordings` (`vocals: false`, a BPM range, a descriptive `term`). After the user picks one, call `DownloadRecording` (`fileType: MP3`, `stemType: FULL`) and curl the returned `assetUrl` into `music/<artist>-<title>.mp3`.
If the download returns **FORBIDDEN**, the account has no active paid subscription. Say so in one line and move to the free sources. Don't retry.

### B. Free music sources (fallback, or when the user asks for free music)
Search these in order, with WebSearch/WebFetch or the browser pane:

| Source | License | Attribution | Notes |
|---|---|---|---|
| **Pixabay Music** — pixabay.com/music | Pixabay Content License: free, commercial use and monetized YouTube allowed | Not required | First choice. Browse by genre/mood, e.g. pixabay.com/music/search/upbeat/ |
| **Incompetech** (Kevin MacLeod) — incompetech.com/music/royalty-free | CC BY 4.0 | **Required** | Direct MP3 links; filter by feel/genre/tempo |
| **Free Music Archive** — freemusicarchive.org | Varies per track | Varies | Only use **CC BY** or **CC0** tracks. Skip anything **NC** (non-commercial) or **ND**, because monetized YouTube counts as commercial |
| **YouTube Audio Library** — studio.youtube.com | Free for YouTube | Some tracks | Needs the user's YouTube login, so recommend tracks for them to download into `music/` themselves |

Rules for free sources:
- Check each track's license **on its own page** before suggesting it. Don't rely on the site's general claim alone.
- Only download from the source's own site or CDN, and only after the user picks a track. If a site needs a login, a CAPTCHA or a "download" form, give the user the link to download it themselves into `music/` instead.
- Never use tracks from random re-upload sites, YouTube rips, or anything with unclear licensing.
- Save as `music/<artist>-<title>.mp3`. If the license needs attribution, write `output/<slug>-credits.txt` with the exact credit line (for Incompetech: `"<Title>" Kevin MacLeod (incompetech.com) Licensed under Creative Commons: By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/`) and tell the user to paste it into their video description.

### C. Last resort
If nothing can be downloaded, build the video without music, and tell the user they can add music later by re-running step 5 with `--music`.

## 4. Graphics (intro/outro)

- Edit the text in `brand/intro.html` (channel name, episode title) and `brand/outro.html` (call to action, handle). Keep `data-composition-id`, the root `data-duration`, and the `window.__timelines` keys unchanged.
- Run `npm run render` → `build/intro.mp4` and `build/outro.mp4` (about 10 seconds).
- For new designs, use the installed `hyperframes*` skills. After edits, run `npx hyperframes lint brand`.
- Check a frame visually: `ffmpeg -v error -y -ss 2 -i build/intro.mp4 -frames:v 1 -vf scale=960:-1 <scratchpad>/intro.png`, then read the image.

## 5. Assemble

```bash
npm run assemble -- --intro build/intro.mp4 --main inbox/<id>.mp4 --outro build/outro.mp4 --music music/<track>.mp3 --out output/<slug>.mp4
```

Everything except `--main` is optional. The script normalizes the segments, joins them, levels the voice to `TARGET_LUFS` (it measures and applies the exact gain, which handles very quiet recordings), ducks the music under speech, and limits peaks to -1.5 dBFS.

## 6. Verify and report

- `ffmpeg -hide_banner -i output/<slug>.mp4 -af ebur128=framelog=quiet:peak=true -f null - 2>&1 | grep -E "^\s+(I|Peak):"` should show about -14 LUFS and a peak below -1 dBFS.
- If the leveling gain was over 20 dB, warn the user that background hiss got louder too, and suggest raising the mic input level next time.
- Report: output path, duration, loudness, the music used (title, artist, source, license), and the credits file if there is one.
- Don't upload or publish anywhere unless the user asks.
