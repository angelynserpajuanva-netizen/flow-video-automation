#!/usr/bin/env bash
# Runs the FFmpeg pipeline on generated test clips, so you can check it works
# before you have any Tella recordings or Epidemic Sound tracks.
set -euo pipefail
cd "$(dirname "$0")/.."
T="build/test"; mkdir -p "$T"

echo "▸ Generating test clips"
# Fake "intro" and "outro" with no audio (like a HyperFrames render)
ffmpeg -v error -y -f lavfi -i "testsrc2=s=1920x1080:r=30:d=3" -pix_fmt yuv420p "$T/intro.mp4"
ffmpeg -v error -y -f lavfi -i "smptebars=s=1920x1080:r=30:d=3" -pix_fmt yuv420p "$T/outro.mp4"
# Fake "Tella recording": 1280x720 at 60fps with a beeping "voice" track
ffmpeg -v error -y -f lavfi -i "testsrc=s=1280x720:r=60:d=8" \
  -f lavfi -i "sine=f=440:d=8,volume='if(lt(mod(t,2),1),1,0)':eval=frame" \
  -shortest -pix_fmt yuv420p -c:a aac "$T/main.mp4"
# Fake "music" track, shorter than the video so the loop is exercised
ffmpeg -v error -y -f lavfi -i "sine=f=220:d=5" -c:a libmp3lame "$T/music.mp3"

bash scripts/assemble.sh --intro "$T/intro.mp4" --main "$T/main.mp4" --outro "$T/outro.mp4" \
  --music "$T/music.mp3" --out "$T/final.mp4"

ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate:format=duration -of compact "$T/final.mp4"
echo "✔ Pipeline works. Expected ~14s at 1920x1080 30fps. Open $T/final.mp4 to check."
