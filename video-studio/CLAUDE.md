# Video Studio — instructions for Claude

This folder is a video editing pipeline: Tella (recording) → HyperFrames (graphics) → music → FFmpeg (assembly). See README.md for setup.

When the user asks for a finished video, music, or intro/outro changes, follow the **`make-video` skill** (`~/.claude/skills/make-video/SKILL.md`). It is the single source of truth for the workflow, including music sourcing: Epidemic Sound first, then free libraries (Pixabay Music, Incompetech, Free Music Archive) when Epidemic Sound can't download.

For AI video made in Google Flow (Omni clips, keyframes, AI voice), follow the **`flow-video` skill** (`~/.claude/skills/flow-video/SKILL.md`) instead: storyboard, keyframes and voice are approved first, then one approved paid clip at a time, then Claude assembles the approved clips directly.

## Rules

- Run commands with `export PATH="/opt/homebrew/bin:$PATH"` first.
- Never print or commit `.env` or API keys.
- Don't delete files in `inbox/`, `music/` or `output/`; `build/work` and `build/test` are disposable.
- Mixing levels live in `.env` (`MUSIC_VOLUME`, `TARGET_LUFS`); change them there rather than editing `scripts/assemble.sh`.
- Show 3 music options and wait for the user's pick before downloading; check each free track's license on its own page.
