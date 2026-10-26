#!/usr/bin/env bash
# Checks that every piece of the video studio is installed and configured.
cd "$(dirname "$0")/.."
ok() { printf "  \033[32m✔\033[0m %s\n" "$1"; }
bad() { printf "  \033[31m✘\033[0m %s\n" "$1"; }

echo "Tools"
if command -v node >/dev/null; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [[ "$major" -ge 22 ]] && ok "Node $(node -v)" || bad "Node $(node -v) — HyperFrames needs 22+ (brew upgrade node)"
else bad "Node not installed (brew install node)"; fi
command -v ffmpeg >/dev/null && ok "FFmpeg $(ffmpeg -version | head -1 | awk '{print $3}')" || bad "FFmpeg not installed (brew install ffmpeg)"
command -v claude >/dev/null && ok "Claude Code CLI" || bad "claude CLI not found (needed for 'claude mcp add')"

echo "Project"
[[ -f .env ]] && ok ".env exists" || bad ".env missing (cp .env.example .env)"
[[ -f .env ]] && grep -q '^TELLA_API_KEY=.\+' .env && ok "TELLA_API_KEY set" || bad "TELLA_API_KEY empty in .env"
[[ -f brand/intro.html && -f brand/outro.html ]] && ok "HyperFrames brand project ready" || bad "brand/ not set up (see README step 4)"

echo "Connections"
[[ -n "${EPIDEMIC_SOUND_API_KEY:-}" ]] && ok "EPIDEMIC_SOUND_API_KEY exported" || bad "EPIDEMIC_SOUND_API_KEY not exported in this shell (~/.zshrc)"
if command -v claude >/dev/null; then
  mcp="$(claude mcp list 2>/dev/null)"
  for s in tella epidemic-sound; do
    echo "$mcp" | grep -q "^$s:.*Connected" && ok "MCP $s connected" || bad "MCP $s not connected (see README)"
  done
fi
