#!/usr/bin/env python3
"""Build a review pack for ONE generated Flow clip, against its approved storyboard scene.

Usage:
  python3 scripts/review_clip.py --storyboard projects/<name>/storyboard.json --scene S03 \
      --clip ~/flow-mcp-out/<Folder>/scene-03.mp4 [--prev-clip FILE] [--out DIR] \
      [--no-transcribe] [--ssim-look 0.70]

Writes projects/<name>/review/<scene>-try<N>/ (report.html, report.json, frame images).
It never calls Flow, never edits storyboard.json, and writes nothing outside its output folder.
Needs only Python 3.9+ and ffmpeg/ffprobe; transcription uses `npx hyperframes transcribe`.

Every check is a pointer for a human, not a verdict: it cannot judge visual quality, natural
motion, likeness, product accuracy, voice consistency or lip sync.
"""
import argparse
import difflib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SHORT_SIDE = {"360p": 360, "720p": 720}
DURATION_TOLERANCE = 0.15  # seconds
LIMITATIONS = [
    "Similarity scores (SSIM) compare grayscale pixels; a high score does not prove the face, "
    "clothing or product are right, and a low score can come from small camera moves.",
    "Freeze and black detection only find frozen or black stretches; they cannot judge natural motion.",
    "The transcript is the clip's own audio. It shows which words were spoken, not voice quality, "
    "voice consistency or lip sync. The approved TTS line is the final voice.",
    "Only you can approve a clip: watch it with sound before deciding.",
]


def run(args, cwd=None):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, cwd=cwd)


def probe(path):
    """Duration, size, fps, exact frame count and audio presence of a clip."""
    out = run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
               "stream=codec_type,width,height,r_frame_rate,nb_read_frames:format=duration",
               "-of", "json", path]).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": round(num / den, 3) if den else None,
        "frames": int(video["nb_read_frames"]),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def image_size(path):
    out = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "csv=p=0", path]).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def grab(clip, index, out):
    """Save exactly frame `index` (0-based) of the clip."""
    run([FFMPEG, "-v", "error", "-y", "-i", clip, "-vf", "select=eq(n\\,%d)" % index,
         "-fps_mode", "passthrough", "-frames:v", "1", out])
    return out


def ssim(a, b, size):
    w, h = size
    graph = "[0:v]scale=%d:%d,format=gray[x];[1:v]scale=%d:%d,format=gray[y];[x][y]ssim" % (w, h, w, h)
    err = run([FFMPEG, "-v", "info", "-i", a, "-i", b, "-lavfi", graph, "-f", "null", "-"]).stderr
    m = re.search(r"All:([0-9.]+)", err)
    return round(float(m.group(1)), 3) if m else None


def side_by_side(a, b, out, size):
    w, h = size
    run([FFMPEG, "-v", "error", "-y", "-i", a, "-i", b, "-filter_complex",
         "[0:v]scale=%d:%d[x];[1:v]scale=%d:%d[y];[x][y]hstack" % (w, h, w, h), "-frames:v", "1", out])
    return out


def strip(clip, frames, out, size):
    w, h = size
    step = max(1, frames // 8)
    run([FFMPEG, "-v", "error", "-y", "-i", clip, "-vf",
         "select='not(mod(n\\,%d))',scale=%d:%d,tile=8x1" % (step, w // 2, h // 2),
         "-fps_mode", "passthrough", "-frames:v", "1", out])
    return out


def detect(clip, filt, pattern):
    err = run([FFMPEG, "-v", "info", "-i", clip, "-vf", filt, "-an", "-f", "null", "-"]).stderr
    return re.findall(pattern, err)


def transcribe(clip, out_dir):
    wav = out_dir / "audio.wav"
    run([FFMPEG, "-v", "error", "-y", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", wav])
    run(["npx", "-y", "hyperframes", "transcribe", "-m", "small.en", "--json", wav.name], cwd=out_dir)
    words = json.loads((out_dir / "transcript.json").read_text())
    return words if isinstance(words, list) else words["words"]


def norm_words(text):
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def compare_words(script, words):
    want = norm_words(script)
    got = norm_words(" ".join(w["text"] for w in words))
    sm = difflib.SequenceMatcher(None, want, got)
    missing, extra = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            missing += want[i1:i2]
        if op in ("insert", "replace"):
            extra += got[j1:j2]
    return {
        "script": script,
        "heard": " ".join(w["text"] for w in words),
        "word_match": round(sm.ratio(), 3),
        "missing": missing,
        "extra": extra,
        "speech_start": words[0]["start"] if words else None,
        "speech_end": words[-1]["end"] if words else None,
    }


def audio_check(info, clip, out_dir, scene, transcribe_enabled=True):
    """Transcript comparison, or a clear note when it can't be done."""
    if not info["has_audio"]:
        return None, ("look", "No audio track in the clip: speech can't be checked here. "
                              "The approved TTS line is the final voice.")
    if not transcribe_enabled:
        return None, ("look", "Transcription skipped (--no-transcribe).")
    if not scene.get("dialogue"):
        return None, ("ok", "Scene has no dialogue; transcription not needed.")
    words = transcribe(clip, out_dir)
    if not words:
        return None, ("look", "Audio present but no words were heard.")
    cmp = compare_words(scene["dialogue"], words)
    level = "ok" if not cmp["missing"] and not cmp["extra"] else "look"
    detail = "Word match %.0f%%; missing: %s; extra: %s. Spoken %.2f-%.2f s of %.2f s." % (
        cmp["word_match"] * 100, " ".join(cmp["missing"]) or "none", " ".join(cmp["extra"]) or "none",
        cmp["speech_start"], cmp["speech_end"], info["duration"])
    return cmp, (level, detail)


def resolve(base, path):
    p = Path(path).expanduser()
    return p if p.is_absolute() else (base / p)


def compare_size(aspect):
    a, b = (int(x) for x in aspect.split(":"))
    return (360, round(360 * b / a)) if b > a else (round(360 * a / b), 360)


def expected_size(video):
    short = SHORT_SIDE.get(video.get("resolution") or "")
    if not short or not video.get("aspect_ratio"):
        return None
    a, b = (int(x) for x in video["aspect_ratio"].split(":"))
    return (short, round(short * b / a)) if b > a else (round(short * a / b), short)


def previous_clip(sb, scene, base):
    earlier = [s for s in sb["scenes"] if s["order"] < scene["order"]]
    if not earlier:
        return None
    prev = max(earlier, key=lambda s: s["order"])
    clip = (prev.get("generation") or {}).get("clip_file")
    if (prev.get("review") or {}).get("status") == "approved" and clip:
        return resolve(base, clip)
    return None


def next_out_dir(base, scene_id):
    n = 1
    while (base / "review" / ("%s-try%d" % (scene_id, n))).exists():
        n += 1
    return base / "review" / ("%s-try%d" % (scene_id, n))


def build(args):
    sb_path = Path(args.storyboard).expanduser().resolve()
    sb = json.loads(sb_path.read_text())
    base = sb_path.parent
    scenes = {s["id"]: s for s in sb["scenes"]}
    if args.scene not in scenes:
        sys.exit("Scene %s not in storyboard (have: %s)" % (args.scene, ", ".join(scenes)))
    scene = scenes[args.scene]
    keyframes = {k["id"]: k for k in sb.get("keyframes", [])}
    video = scene.get("video") or {}
    clip = Path(args.clip).expanduser().resolve()
    if not clip.exists():
        sys.exit("Clip not found: %s" % clip)

    out = Path(args.out).expanduser().resolve() if args.out else next_out_dir(base, scene["id"])
    out.mkdir(parents=True, exist_ok=False)
    checks = []

    def add(name, level, detail):
        checks.append({"check": name, "level": level, "detail": detail})

    info = probe(clip)
    cmp_size = compare_size(video.get("aspect_ratio") or "9:16")

    # Settings the card promised vs the file that came back.
    missing = [k for k in ("model", "aspect_ratio", "duration_seconds", "resolution") if not video.get(k)]
    if missing:
        add("Video settings", "look", "Scene's video block is missing: %s." % ", ".join(missing))
    want_d = video.get("duration_seconds")
    if want_d:
        off = abs(info["duration"] - want_d)
        add("Duration", "ok" if off <= DURATION_TOLERANCE else "look",
            "%.3f s (%d frames at %s fps); card said %s s." % (info["duration"], info["frames"], info["fps"], want_d))
    want_wh = expected_size(video)
    if want_wh:
        same = (info["width"], info["height"]) == want_wh
        add("Size", "ok" if same else "look", "%dx%d; card said %s %s = %dx%d." % (
            info["width"], info["height"], video["resolution"], video["aspect_ratio"], want_wh[0], want_wh[1]))

    # Exact first, middle and true last frames.
    first = grab(clip, 0, out / "first.png")
    mid = grab(clip, info["frames"] // 2, out / "middle.png")
    last = grab(clip, info["frames"] - 1, out / "last.png")
    strip(clip, info["frames"], out / "strip.png", cmp_size)

    pairs = []

    def keyframe_pair(label, kf_id, frame, png):
        kf = keyframes.get(kf_id or "")
        if not kf or not kf.get("file"):
            add(label, "look", "Keyframe %s has no file in the storyboard; nothing to compare." % kf_id)
            return
        path = resolve(base, kf["file"])
        if not path.exists():
            add(label, "look", "Keyframe file missing: %s" % path)
            return
        kw, kh = image_size(path)
        note = ""
        if abs(kw / kh - info["width"] / info["height"]) > 0.02:
            note = " Keyframe is %dx%d, a different shape from the clip, so this compares stretched images." % (kw, kh)
        score = ssim(str(path), frame, cmp_size)
        side_by_side(str(path), frame, out / png, cmp_size)
        level = "ok" if score is not None and score >= args.ssim_look and not note else "look"
        add(label, level, "SSIM %s vs %s (look below %.2f).%s" % (score, kf_id, args.ssim_look, note))
        pairs.append((label, png, "%s (left) vs clip (right)" % kf_id))

    keyframe_pair("Start frame vs start keyframe", scene.get("start_keyframe"), first, "start_vs_keyframe.png")
    keyframe_pair("End frame vs end keyframe", scene.get("end_keyframe"), last, "end_vs_keyframe.png")

    prev = Path(args.prev_clip).expanduser().resolve() if args.prev_clip else previous_clip(sb, scene, base)
    if prev and prev.exists():
        pinfo = probe(prev)
        prev_last = grab(prev, pinfo["frames"] - 1, out / "previous_last.png")
        score = ssim(prev_last, first, cmp_size)
        side_by_side(prev_last, first, out / "join_with_previous.png", cmp_size)
        add("Join with previous clip", "ok" if score is not None and score >= args.ssim_look else "look",
            "SSIM %s between the previous clip's last frame and this clip's first frame." % score)
        pairs.append(("Join with previous clip", "join_with_previous.png", "previous last (left) vs this first (right)"))
    else:
        add("Join with previous clip", "ok", "No approved previous clip to compare.")

    freezes = detect(clip, "freezedetect=n=0.003:d=1.0", r"freeze_duration: ([0-9.]+)")
    add("Frozen stretches", "look" if freezes else "ok",
        ("Frozen for %s s." % ", ".join(freezes)) if freezes else "None of 1 s or longer found.")
    blacks = detect(clip, "blackdetect=d=0.3:pix_th=0.10", r"black_duration:([0-9.]+)")
    add("Black stretches", "look" if blacks else "ok",
        ("Black for %s s." % ", ".join(blacks)) if blacks else "None of 0.3 s or longer found.")

    words, (level, detail) = audio_check(info, clip, out, scene, not args.no_transcribe)
    add("Spoken words vs script", level, detail)

    report = {"scene": scene["id"], "beat": scene.get("beat"), "clip": str(clip), "video": video,
              "probe": info, "checks": checks, "words": words, "limitations": LIMITATIONS,
              "ssim_look_threshold": args.ssim_look}
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.html").write_text(render(report, pairs))
    return out, report


def render(r, pairs):
    e = html.escape
    rows = "".join(
        "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            c["level"], e(c["check"]), "check" if c["level"] == "look" else "ok", e(c["detail"]))
        for c in r["checks"])
    imgs = "".join("<figure><figcaption>%s: %s</figcaption><img src='%s'></figure>" % (e(t), e(c), e(p))
                   for t, p, c in pairs)
    words = ""
    if r["words"]:
        w = r["words"]
        words = "<h2>Dialogue</h2><p><b>Script:</b> %s</p><p><b>Heard:</b> %s</p>" % (e(w["script"]), e(w["heard"]))
    v = r["video"]
    return """<!doctype html><meta charset="utf-8"><title>Review %s</title>
<style>body{font:15px/1.5 -apple-system,sans-serif;margin:24px;max-width:1100px;color:#1d1d1f;background:#fff}
table{border-collapse:collapse;width:100%%}td{border-top:1px solid #ddd;padding:6px 8px;vertical-align:top}
tr.look td:nth-child(2){color:#b45309;font-weight:600}img{max-width:100%%;border:1px solid #ccc}
figure{margin:16px 0}.note{background:#fff7e6;padding:10px 12px;border-radius:6px}</style>
<h1>Review: %s %s</h1>
<p>%s · %s s · %s · %s · clip <code>%s</code></p>
<p class="note">These checks point at things to look at. They do not approve the clip. Watch it with sound and decide.</p>
<table>%s</table>
<h2>Frames</h2>%s
<figure><figcaption>Whole clip, 8 evenly spaced frames</figcaption><img src="strip.png"></figure>
<figure><figcaption>Middle frame</figcaption><img src="middle.png" style="max-width:360px"></figure>
%s
<h2>What these checks can't tell you</h2><ul>%s</ul>
""" % (e(r["scene"]), e(r["scene"]), e(r.get("beat") or ""), e(str(v.get("model"))),
       e(str(v.get("duration_seconds"))), e(str(v.get("resolution"))), e(str(v.get("aspect_ratio"))),
       e(r["clip"]), rows, imgs, words, "".join("<li>%s</li>" % e(x) for x in r["limitations"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--prev-clip")
    ap.add_argument("--out")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--ssim-look", type=float, default=0.70)
    out, report = build(ap.parse_args())
    looks = [c for c in report["checks"] if c["level"] == "look"]
    print("Review pack: %s/report.html" % out)
    for c in report["checks"]:
        print("  [%s] %s: %s" % ("check" if c["level"] == "look" else " ok  ", c["check"], c["detail"]))
    print("%d item(s) to look at. Watch the clip with sound before deciding." % len(looks))


if __name__ == "__main__":
    main()
