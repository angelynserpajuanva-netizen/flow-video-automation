#!/usr/bin/env bash
# Assemble a finished video with FFmpeg:
#   [intro] + main recording + [outro], with an optional music bed that
#   automatically ducks under your voice, then loudness-normalized for YouTube.
#
# Usage:
#   npm run assemble -- --main inbox/VIDEO.mp4 [--music music/TRACK.mp3] \
#                       [--intro build/intro.mp4] [--outro build/outro.mp4] \
#                       [--out output/final.mp4]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

W="${VIDEO_WIDTH:-1920}"; H="${VIDEO_HEIGHT:-1080}"; FPS="${VIDEO_FPS:-30}"
MUSIC_VOLUME="${MUSIC_VOLUME:-0.25}"; TARGET_LUFS="${TARGET_LUFS:-14}"
TARGET_LUFS="-${TARGET_LUFS#-}"   # accept 14 or -14

MAIN=""; MUSIC=""; INTRO=""; OUTRO=""; OUT="output/final-$(date +%Y%m%d-%H%M%S).mp4"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --main)  MAIN="$2"; shift 2 ;;
    --music) MUSIC="$2"; shift 2 ;;
    --intro) INTRO="$2"; shift 2 ;;
    --outro) OUTRO="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done
[[ -z "$MAIN" ]] && { echo "Missing --main <video>"; exit 1; }
for f in "$MAIN" "$MUSIC" "$INTRO" "$OUTRO"; do
  if [[ -n "$f" && ! -f "$f" ]]; then echo "File not found: $f"; exit 1; fi
done

WORK="build/work"; rm -rf "$WORK"; mkdir -p "$WORK" "$(dirname "$OUT")"

duration() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
has_audio() { [[ -n "$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$1")" ]]; }

# 1) Normalize every segment to the same size, frame rate and audio format so they join cleanly.
normalize() {
  local in="$1" out="$2"
  local vf="scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=${FPS},format=yuv420p"
  local enc=(-c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k -ar 48000 -ac 2 -movflags +faststart)
  if has_audio "$in"; then
    ffmpeg -v error -y -i "$in" -vf "$vf" -map 0:v:0 -map 0:a:0 "${enc[@]}" "$out"
  else
    # HyperFrames renders may have no audio track; add silence so concat stays in sync.
    ffmpeg -v error -y -i "$in" -f lavfi -i anullsrc=r=48000:cl=stereo \
      -vf "$vf" -map 0:v:0 -map 1:a:0 -shortest "${enc[@]}" "$out"
  fi
}

LIST="$WORK/concat.txt"; : > "$LIST"; i=0
for seg in "$INTRO" "$MAIN" "$OUTRO"; do
  [[ -z "$seg" ]] && continue
  i=$((i+1)); echo "▸ Normalizing $seg"
  normalize "$seg" "$WORK/seg$i.mp4"
  echo "file '$ROOT/$WORK/seg$i.mp4'" >> "$LIST"
done

# 2) Join the segments.
echo "▸ Joining $i segment(s)"
ffmpeg -v error -y -f concat -safe 0 -i "$LIST" -c copy "$WORK/joined.mp4"

# 3) Bring audio to the target loudness: measure it, apply the exact gain, and limit peaks
#    to -1.5 dBFS. (Single-pass loudnorm can't lift very quiet recordings far enough.)
lufs() { ffmpeg -hide_banner -nostats -i "$1" -af ebur128=framelog=quiet -f null - 2>&1 | awk '$1 == "I:" { v = $2 } END { print v }'; }
level() {
  local in="$1" out="$2" gain
  gain="$(awk -v m="$(lufs "$in")" -v t="$TARGET_LUFS" 'BEGIN { if (m == "" || m == "-inf" || m < -70) print 0; else printf "%.2f", t - m }')"
  echo "  gain ${gain} dB"
  ffmpeg -v error -y -i "$in" -vn -af "volume=${gain}dB,alimiter=limit=0.84:level=false,aresample=48000" -c:a pcm_s16le "$out"
}

echo "▸ Leveling voice to ${TARGET_LUFS} LUFS"
level "$WORK/joined.mp4" "$WORK/voice.wav"
FINAL_AUDIO="$WORK/voice.wav"

# 4) Mix music (looped, faded, ducked under the leveled voice), then re-level the mix.
if [[ -n "$MUSIC" ]]; then
  echo "▸ Mixing music bed: $MUSIC"
  DUR="$(duration "$WORK/joined.mp4")"
  FADE_ST="$(awk -v d="$DUR" 'BEGIN { s = d - 3; if (s < 0) s = 0; printf "%.3f", s }')"
  ffmpeg -v error -y -i "$WORK/voice.wav" -stream_loop -1 -i "$MUSIC" -filter_complex "
    [1:a]aresample=48000,atrim=0:${DUR},volume=${MUSIC_VOLUME},afade=t=in:d=1,afade=t=out:st=${FADE_ST}:d=3[bed];
    [0:a]asplit=2[voice][key];
    [bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked];
    [voice][ducked]amix=inputs=2:duration=first:normalize=0[mix]" \
    -map "[mix]" -c:a pcm_s16le "$WORK/mix.wav"
  level "$WORK/mix.wav" "$WORK/final.wav"
  FINAL_AUDIO="$WORK/final.wav"
fi

ffmpeg -v error -y -i "$WORK/joined.mp4" -i "$FINAL_AUDIO" -map 0:v -map 1:a \
  -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart "$OUT"

echo "✔ Done: $OUT ($(printf '%.1f' "$(duration "$OUT")")s)"
