# Flow Video Automation

Let Claude make and edit AI videos in **Google Flow** for you: storyboard → consistent characters →
free still frames → video clips with dialogue and sound → narration → music → final cut.

Built on [flow-mcp](https://github.com/GTAI-1/flow-mcp) by GTAI-1, plus a workflow and editing scripts.

| Part | What it is | Where it goes |
|---|---|---|
| flow-mcp (downloaded in step 3) | Drives Google Flow in its own Chrome window | `~/mcp-servers/flow-mcp` |
| `flow-mcp-menu-fix.patch` | Our fix for Flow's press-and-hold menu that blocked saving | applied in step 3 |
| `video-studio/` | Editing scripts, templates, intro/outro graphics | `~/Claude Code/video-studio` |
| `claude-skills/` | Teaches Claude the workflow (`flow-video`, `make-video`) | `~/.claude/skills/` |

`~` means your home folder (in Finder: **Go → Home**). These steps are for a **Mac**.

---

## 1. Install the tools

You need the **Claude desktop app** (or Claude Code), **Google Chrome**, and a **Google account**
(free accounts get 50 Flow credits a day; Google AI Pro gives more).

Open **Terminal** and run these one at a time. Skip any you already have.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```bash
brew install node ffmpeg python git
```

Check Node is version 22 or newer:

```bash
node --version
```

## 2. Download this repo and copy the parts into place

```bash
git clone https://github.com/angelynserpajuanva-netizen/flow-video-automation.git ~/flow-video-automation
```

```bash
mkdir -p ~/"Claude Code" ~/.claude/skills ~/mcp-servers
```

```bash
cp -R ~/flow-video-automation/video-studio ~/"Claude Code"/
```

```bash
cp -R ~/flow-video-automation/claude-skills/* ~/.claude/skills/
```

```bash
npx skills add heygen-com/hyperframes --all
```

(The last one installs the HyperFrames skills, used for graphics, captions and audio mixing.)

## 3. Install flow-mcp with the menu fix

```bash
git clone https://github.com/GTAI-1/flow-mcp.git ~/mcp-servers/flow-mcp
```

```bash
cd ~/mcp-servers/flow-mcp && git checkout 4e5e2b2daf30ae9166386af080ff3a1681e99607 && git apply ~/flow-video-automation/flow-mcp-menu-fix.patch
```

```bash
cd ~/mcp-servers/flow-mcp && npm install && npm run build
```

> If `git apply` fails, skip it — flow-mcp still works, Flow's pop-up menu may just need closing by hand sometimes.
> Keep flow-mcp out of Desktop, Documents and Downloads: macOS blocks it from reading its own files there.

## 4. Connect it to Claude

**Claude Code (terminal):**

```bash
claude mcp add --scope user flow -- node ~/mcp-servers/flow-mcp/dist/index.js
```

**Claude desktop app** (if that says `command not found`), ask Claude:

> *Add the flow MCP server at ~/mcp-servers/flow-mcp/dist/index.js to my user config.*

## 5. Sign in to Flow (once)

```bash
cd ~/mcp-servers/flow-mcp && npm run login
```

A separate Chrome window opens. Sign in to Google **yourself**, open or create a **Flow project**, and
leave that window open while you make videos. The login stays only on your computer.

## 6. Restart Claude and test

Quit and reopen Claude, open the `~/Claude Code/video-studio` folder, and ask:

> **check my Flow session**

You should see "signed in", your plan, and your credits left.

---

## Making a video

Ask in plain English, for example:

> *Make a 30-second ad from this storyboard: … Keep the character consistent, add dialogue, sound effects and background music. 9:16 for Reels.*

Claude follows the `flow-video` skill:

1. Writes a storyboard and checks it — you approve it.
2. Creates the characters and draws free still frames — you approve the look.
3. Makes each paid video clip **one at a time, only after you say yes** (it tells you the cost first).
4. Adds voice, music and captions, and joins everything into one MP4.

Stills, characters, narration and editing are **free**. Clips on Omni 1.1 Flash cost about
7 credits (4 s) to 12 credits (8 s).

## Optional extras

- **Control panel without Claude:** double-click `~/mcp-servers/flow-mcp/Flow Studio.command`, then
  open http://127.0.0.1:8787 (right-click → Open the first time if macOS warns you).
- **Google narration voices:** get a free key at https://aistudio.google.com and paste it on the
  panel's **Voice** tab. Never paste keys into a chat. Without a key, the Mac's own voices work.
- **Tella recordings (`make-video` skill):** copy `video-studio/.env.example` to `video-studio/.env`
  and add your own Tella API key. See `video-studio/README.md`.
- **Your own intro/outro:** edit `video-studio/brand/intro.html` and `outro.html` (or ask Claude to).

## Troubleshooting

- **"No project is open"** → open a project in the Flow Chrome window.
- **Flow seems stuck / a button can't be clicked** → reload the Flow page, scroll to the top, ask Claude to try again.
- **Flow tools don't show up in Claude** → fully quit and reopen Claude after step 4.
- **Where are my files?** → `~/flow-mcp-out/<project>/` and `~/Claude Code/video-studio/projects/`.
