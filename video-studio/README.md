# Video Studio

A video editing system that combines four tools:

| Tool | Job in this system |
|---|---|
| **Tella** | Record and rough-cut your screen/camera video. Pulled down automatically via the Tella API. |
| **HyperFrames** | Branded motion graphics (intro, outro, lower thirds) written as HTML and rendered to MP4. |
| **Epidemic Sound** | Licensed background music, found and downloaded by Claude through Epidemic Sound's MCP server. |
| **FFmpeg** | Glues it together: intro + recording + outro, music that ducks under your voice, loudness set for YouTube. |

```
 Tella recording ──(npm run tella:pull)──▶ inbox/
 HyperFrames  ─────(npm run render)──────▶ build/intro.mp4, build/outro.mp4
 Epidemic Sound ───(ask Claude)──────────▶ music/
                                              │
                         npm run assemble ◀───┘
                                              ▼
                                       output/final.mp4
```

---

## Setup, step by step

Run each command in **Terminal** (Applications → Utilities → Terminal). Paste one block at a time.

### Step 1 — Install Homebrew (the Mac package manager)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It will ask for your Mac password and may install Apple's Command Line Tools. That's expected.
When it finishes, it prints two "Next steps" lines. Run them. On Apple Silicon Macs they are:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
```

```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### Step 2 — Install Node.js and FFmpeg

```bash
brew install node ffmpeg
```

Check them (Node must be **22 or newer** for HyperFrames):

```bash
node -v && ffmpeg -version | head -1
```

Install the Claude Code command-line tool. Steps 4–6 use it to connect HyperFrames, Tella and Epidemic Sound:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Close Terminal and open a new window, then check it with `claude --version`.

### Step 3 — Test the FFmpeg pipeline (no accounts needed)

```bash
cd ~/"Claude Code"/video-studio && npm run test:pipeline
```

This generates fake clips and runs the whole assembly. Open `build/test/final.mp4`. You should see a test pattern intro,
a bars outro, a beeping "voice", and a quieter tone underneath that dips whenever the beep plays. That dip is the music ducking.

### Step 4 — Set up HyperFrames (intro & outro graphics)

Create the HyperFrames project inside the studio (answer **y** when npm asks to install `hyperframes`):

```bash
cd ~/"Claude Code"/video-studio && npx hyperframes init brand
```

This also installs HyperFrames' skills for Claude (into `~/.claude/skills/`), so Claude can design new graphics for you.
When it finishes it opens a live preview and keeps running. Press **Control + C** to stop it, then copy in the starter templates:

```bash
cp templates/intro.html templates/outro.html brand/
```

Render both graphics (takes about 10 seconds):

```bash
npm run render
```

You'll get `build/intro.mp4` and `build/outro.mp4`. To change the text or colors, edit `brand/intro.html` and `brand/outro.html`
(the `:root` colors and the text inside the tags), then run `npm run preview` to watch it live in your browser.

### Step 5 — Connect Tella

1. In Tella, open **Settings → API** and generate an API key (it starts with `tella_pk_`).
2. Create your `.env` file:

   ```bash
   cd ~/"Claude Code"/video-studio && cp .env.example .env && open -e .env
   ```

3. Paste the key after `TELLA_API_KEY=` and save.
4. Test it. This lists your Tella videos and their IDs:

   ```bash
   npm run tella:list
   ```

5. **Optional:** connect Tella's MCP server so Claude can also edit in Tella (remove filler words, add zooms and chapters, read transcripts):

   ```bash
   claude mcp add --transport http --scope user tella https://api.tella.com/mcp
   ```

   It opens a browser window so you can log in to Tella.

### Step 6 — Connect Epidemic Sound

Downloading music requires an **active paid Epidemic Sound subscription**. Searching and previewing work on a free account.

1. In Epidemic Sound, open your **account settings** and generate an API key. Keys expire after one year.
2. Save the key to your shell profile. Replace `YOUR_KEY` with the key:

   ```bash
   echo 'export EPIDEMIC_SOUND_API_KEY="YOUR_KEY"' >> ~/.zshrc && source ~/.zshrc
   ```

3. Register the MCP server. Keep the single quotes exactly as written:

   ```bash
   claude mcp add -s user --transport http epidemic-sound https://www.epidemicsound.com/a/mcp-service/mcp --header 'Authorization: Bearer ${EPIDEMIC_SOUND_API_KEY}'
   ```

4. **Licensing:** if you publish to YouTube, connect your channel in Epidemic Sound (Account → Channels) so you don't get Content ID claims.

### Step 7 — Check everything

```bash
cd ~/"Claude Code"/video-studio && npm run doctor
```

Every line should show a green ✔. Restart the Claude app afterwards so it picks up the new MCP servers.

---

## Making a video (everyday workflow)

1. **Record in Tella** and do your rough cut there: trim mistakes, remove filler words, set layouts.
2. **Pull it down:** run `npm run tella:list` to find the ID, then:
   ```bash
   npm run tella:pull -- vid_XXXXXXXX
   ```
   Add `--4k`, `--60fps` or `--subtitles` if you want them. The file lands in `inbox/vid_XXXXXXXX.mp4`.
3. **Get music:** in Claude, open the `video-studio` folder and ask, for example:
   *"Find a calm lo-fi track around 90 BPM on Epidemic Sound, about 5 minutes long, and save it to music/."*
4. **Update graphics** (if needed): edit `brand/intro.html` with the episode title, then `npm run render`.
5. **Assemble:**
   ```bash
   npm run assemble -- --intro build/intro.mp4 --main inbox/vid_XXXXXXXX.mp4 --outro build/outro.mp4 --music music/TRACK.mp3 --out output/my-video.mp4
   ```
   Every part except `--main` is optional.

Or just ask Claude: *"Make a finished video from my latest Tella recording with upbeat music and the intro titled 'How I plan my week'."*
It follows the steps in [CLAUDE.md](CLAUDE.md).

### Tuning

All in `.env`:

| Setting | Default | What it does |
|---|---|---|
| `MUSIC_VOLUME` | `0.25` | How loud the music is before ducking. Try `0.15` for talk-heavy videos. |
| `TARGET_LUFS` | `-14` | Final loudness. Use `-14` for YouTube, `-16` for podcasts. |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `1920` / `1080` | Use `1080` / `1920` for vertical Shorts and Reels. Update the HyperFrames sizes to match. |
| `VIDEO_FPS` | `30` | Output frame rate. |

## Folder layout

```
video-studio/
├── inbox/        Tella exports land here
├── music/        Epidemic Sound tracks
├── brand/        HyperFrames project (created in step 4)
├── templates/    Starter intro/outro compositions
├── build/        Rendered graphics + temp files
├── output/       Finished videos
└── scripts/      tella.mjs, assemble.sh, doctor.sh, test-pipeline.sh
```

## Troubleshooting

- **`npm run tella:pull` says 403:** the video's download settings in Tella don't allow exports, or you're only a viewer on it.
- **4K or 60fps export fails:** those need a paid Tella plan.
- **`epidemic-sound` shows "Failed to connect":** run `echo $EPIDEMIC_SOUND_API_KEY`. If it's empty, redo step 6.2 and open a new Terminal window.
- **Tella MCP login gets stuck:** run `rm -rf ~/.mcp-auth` and add it again.
- **HyperFrames render errors:** run `npx hyperframes lint brand` from the `video-studio` folder to see what's wrong in the HTML.
