#!/usr/bin/env python3
"""Assemble a project's approved Flow clips into one finished video, driven by its storyboard.json.

Usage (copy-paste; "cozy-mug" is the example project folder, put yours there):
  cd ~/"Claude Code"/video-studio
  python3 scripts/assemble.py projects/cozy-mug/storyboard.json --out projects/cozy-mug/renders/v1
  options: --no-transcribe (skip the dialogue check; captions need it), --guard PATH (credit guard to
           price-check against, default ~/.claude/hooks/flow_guard.py)

Read-only on every input. Writes only inside --out, which must not exist yet (no overwrites).
Runs scripts/storyboard_check.py first and stops on any error. Uses only ffmpeg/ffprobe, plus
HyperFrames (npx) for captions and transcription. Never calls Flow.

What it does (the pipeline validated on the Heating Tray previews):
  - every scene whose review is "approved", in storyboard order; the clips' own audio is dropped
  - one shared voice chain on every line, each line normalised to the same loudness
  - one continuous room-tone track under everything, a fixed distance below the voice
  - optional hold of the final frame with picture and sound fading out together
  - optional HyperFrames captions from given phrases, timed from the voice transcript, with any start
    the transcriber put inside a pause moved to the measured speech onset (ffmpeg silencedetect)
  - checks: loudness, durations, frame similarity at each join, room-tone steadiness,
    transcript vs dialogue, caption timing; contact sheets of captions and the ending

Settings: storyboard "assembly" block (all optional; safe defaults shown):
  room_tone: null                  file looped under the whole video (none = no room tone, warned)
  room_tone_below_voice_db: 17     line_lufs: -20     final_lufs: -14     fps: 24
  voice_chain: "default"           or an ffmpeg audio filter string applied to every line
  end_hold: 0, end_fade: 0.8       seconds; hold > 0 freezes the last frame, then fades out
  captions: {enabled: false, phrases: [...], top: 960, box: true, zones: [[from_s, to_s, top], ...]}
Per scene: generation.clip_file (the approved clip), voice_file (optional lip-fitted line, else
tts_file; it must start at the clip's start), trim_end (optional, seconds kept from the clip),
trim_start (optional, seconds dropped from the clip's head; the kept part runs trim_start..trim_end).
A trim that shortens a clip which meets the next scene is refused unless that next scene has
join_previous "cut" (an approved hard cut); a start trim likewise needs join_previous "cut" on the
trimmed scene itself, unless it is the first scene. Either trim is refused if it would cut speech.
grade (optional): an ffmpeg video filter chain applied to that scene's picture only, after scaling
(e.g. "eq=saturation=1.06"), for matching colour between shots; it never changes timing.
See the flow-video skill's join rules.
"""
import argparse
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CHAIN = ("highpass=f=80,lowpass=f=12000,"
                 "acompressor=threshold=0.1:ratio=3:attack=5:release=100:makeup=1,"
                 "aecho=0.8:0.5:18|35:0.10|0.06")
DEFAULTS = {"room_tone": None, "room_tone_below_voice_db": 17.0, "line_lufs": -20.0, "final_lufs": -14.0,
            "fps": 24, "voice_chain": "default", "end_hold": 0.0, "end_fade": 0.8,
            "captions": {"enabled": False, "phrases": [], "top": 960, "box": True, "zones": []}}


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*args, cwd=None):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, cwd=cwd)


def loudness(path):
    err = run("ffmpeg", "-v", "info", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-").stderr
    summary = err[err.rfind("Summary:"):]
    val = lambda m: float(m.group(1)) if m and m.group(1) != "-inf" else float("-inf")
    return (val(re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)),
            val(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)))


def rms_db(path, start, dur):
    err = run("ffmpeg", "-v", "info", "-ss", "%.3f" % max(start, 0), "-t", "%.3f" % dur, "-i", path,
              "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-").stderr
    m = re.findall(r"RMS level dB:\s+(-?[\d.]+|-inf)", err)
    return float(m[-1]) if m and m[-1] != "-inf" else float("-inf")


def duration(path, stream=None):
    sel = ["-select_streams", stream, "-show_entries", "stream=duration"] if stream else ["-show_entries", "format=duration"]
    return float(run("ffprobe", "-v", "error", *sel, "-of", "csv=p=0", path).stdout.strip().split("\n")[0])


class Fail(Exception):
    pass


def settings(sb):
    cfg = json.loads(json.dumps(DEFAULTS))
    user = sb.get("assembly") or {}
    for k in DEFAULTS:
        if k in user and k != "captions":
            cfg[k] = user[k]
    cfg["captions"].update(user.get("captions") or {})
    w, h = (int(x) for x in re.match(r"(\d+)x(\d+)$", (sb.get("format") or {}).get("final_resolution", "1080x1920")).groups())
    cfg["size"] = (w, h)
    return cfg


def plan(sb, base, path):
    """Approved scenes with resolved files and kept lengths; raises Fail with every problem found."""
    problems, used = [], []
    scenes = sb.get("scenes") or []
    for s in scenes:
        rev = (s.get("review") or {}).get("status")
        if rev not in ("approved", "rejected"):
            problems.append("scene %s is not reviewed (%s); assemble only after every scene is approved or rejected" % (s.get("id"), rev))
    approved = [s for s in scenes if (s.get("review") or {}).get("status") == "approved"]
    if not approved:
        problems.append("no approved scenes to assemble")
    for i, s in enumerate(approved):
        sid = s["id"]
        clip = path((s.get("generation") or {}).get("clip_file") or "")
        if not clip.is_file():
            problems.append("scene %s: clip file missing (%s)" % (sid, clip))
            continue
        clip_len = duration(clip, "v:0")
        keep = s.get("trim_end", clip_len)
        if not isinstance(keep, (int, float)) or isinstance(keep, bool) or not 0.5 <= keep <= clip_len + 0.01:
            problems.append("scene %s: trim_end %s must be between 0.5 and the clip length %.3f" % (sid, keep, clip_len))
            continue
        keep = min(keep, clip_len)
        start = s.get("trim_start", 0)
        if not isinstance(start, (int, float)) or isinstance(start, bool) or not 0 <= start <= keep - 0.5:
            problems.append("scene %s: trim_start %s must be between 0 and trim_end - 0.5 (%.3f)" % (sid, start, keep - 0.5))
            continue
        prev = approved[i - 1] if i else None
        if start > 0.05 and prev is not None and s.get("join_previous") != "cut":
            problems.append("join %s -> %s no longer matches (scene %s starts %.3f s into its clip); set join_previous: "
                            "\"cut\" on %s only if that hard cut is approved" % (prev["id"], sid, sid, start, sid))
        nxt = approved[i + 1] if i + 1 < len(approved) else None
        if nxt is not None:
            adjacent = scenes.index(nxt) == scenes.index(s) + 1
            if (not adjacent or keep < clip_len - 0.05) and nxt.get("join_previous") != "cut":
                why = "a scene between them was rejected" if not adjacent else "scene %s is trimmed to %.3f of %.3f s" % (sid, keep, clip_len)
                problems.append("join %s -> %s no longer matches (%s); set join_previous: \"cut\" on %s only if that hard "
                                "cut is approved" % (sid, nxt["id"], why, nxt["id"]))
        voice = None
        if s.get("dialogue"):
            voice = path(s.get("voice_file") or s.get("tts_file") or "")
            if not voice.is_file():
                problems.append("scene %s: voice file missing (%s)" % (sid, voice))
                continue
            end = audible_end(voice)
            if keep < end - 0.01:  # 10 ms = the fade-out applied at the cut
                problems.append("scene %s: the voice line runs past the kept clip (audible until %.2f s, trim at %.2f s); "
                                "the trim would cut speech" % (sid, end, keep))
            onset = audible_start(voice)
            if start > onset + 0.01:
                problems.append("scene %s: the voice line starts before the kept clip (audible from %.2f s, start trim at "
                                "%.2f s); the start trim would cut speech" % (sid, onset, start))
        grade = s.get("grade") or ""
        if not isinstance(grade, str) or any(ch in grade for ch in ";[]"):
            problems.append("scene %s: grade must be a single ffmpeg filter chain (no ';', '[' or ']')" % sid)
            continue
        used.append({"id": sid, "clip": clip, "start": start, "keep": keep, "voice": voice,
                     "dialogue": s.get("dialogue") or "", "grade": grade})
    if problems:
        raise Fail("\n".join(problems))
    return used


def render(sb, base, out, cfg, used):
    """The validated preview pipeline, generalised. Returns the report dict."""
    rep = {"project": sb.get("project"), "settings": {k: v for k, v in cfg.items() if k != "size"},
           "lines": {}, "joins": [], "final": {}}
    chain = DEFAULT_CHAIN if cfg["voice_chain"] == "default" else cfg["voice_chain"]
    for u in used:  # 1. one chain on every line, then the same loudness
        if not u["voice"]:
            continue
        tmp, norm = out / ("_chain_%s.wav" % u["id"]), out / ("voice_%s.wav" % u["id"])
        run("ffmpeg", "-v", "error", "-y", "-i", u["voice"], "-af", chain, "-ar", "48000", "-ac", "1", tmp)
        lufs, _ = loudness(tmp)
        run("ffmpeg", "-v", "error", "-y", "-i", tmp, "-af", "volume=%.2fdB" % (cfg["line_lufs"] - lufs), norm)
        tmp.unlink()
        before, after = loudness(u["voice"])[0], loudness(norm)
        rep["lines"][u["id"]] = {"source": str(u["voice"]), "lufs_before": round(before, 1),
                                 "lufs_after": round(after[0], 1), "true_peak": round(after[1], 1)}
        u["norm"] = norm
    # 2. cut video and voice to the same lengths and join them in order
    w, h = cfg["size"]
    fps, hold, fade = cfg["fps"], float(cfg["end_hold"]), float(cfg["end_fade"])
    inputs, vf, af, labels, t = [], [], [], [], 0.0
    for n, u in enumerate(used):
        vi = 2 * n  # each scene adds two inputs: its clip, then its voice (or a silent source)
        inputs += ["-i", u["clip"]]
        last = n == len(used) - 1
        pad_v = ",tpad=stop_mode=clone:stop_duration=%.3f" % hold if last and hold else ""
        a, b = u["start"], u["keep"]
        grade = "," + u["grade"] if u["grade"] else ""
        vf.append("[%d:v]trim=%.4f:%.4f,setpts=PTS-STARTPTS,scale=%d:%d%s,fps=%s%s[v%d]" % (vi, a, b, w, h, grade, fps, pad_v, n))
        if u.get("norm"):
            inputs += ["-i", u["norm"]]
        else:
            inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
        src = "[%d:a]atrim=%.4f:%.4f,asetpts=PTS-STARTPTS" % (vi + 1, a, b)
        pad_a = ",apad=pad_dur=%.3f" % hold if last and hold else ""
        af.append("%s,afade=t=in:d=0.01,afade=t=out:st=%.4f:d=0.01%s[a%d]" % (src, b - a - 0.01, pad_a, n))
        labels.append("[v%d][a%d]" % (n, n))
        if n:
            rep["joins"].append({"at": round(t, 3), "into": u["id"]})
        t += b - a
    total = t + hold
    tail = "[v][a]" if not hold else "[vc][a];[vc]fade=t=out:st=%.3f:d=%.3f[v]" % (total - fade, fade)
    graph = ";".join(vf + af) + ";" + "".join(labels) + "concat=n=%d:v=1:a=1%s" % (len(used), tail)
    staged = out / "_video_voice.mov"
    run("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", staged)
    voice_track, bed_track, mix = out / "voice_track.wav", out / "bed_track.wav", out / "mix.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", staged, "-vn", "-ac", "1", "-ar", "48000", voice_track)
    # 3. one continuous room tone under everything, then the final loudness
    if cfg["room_tone"]:
        bed = Path(os.path.expanduser(cfg["room_tone"]))
        bed = bed if bed.is_absolute() else base / bed
        bed_lufs, _ = loudness(bed)
        run("ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", bed, "-af",
            "atrim=0:%.4f,volume=%.2fdB,afade=t=in:d=0.5,afade=t=out:st=%.4f:d=%.3f,aformat=channel_layouts=mono"
            % (total, cfg["line_lufs"] - cfg["room_tone_below_voice_db"] - bed_lufs, total - fade, fade),
            "-ar", "48000", bed_track)
    else:
        run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "%.4f" % total, bed_track)
    run("ffmpeg", "-v", "error", "-y", "-i", voice_track, "-i", bed_track, "-filter_complex",
        "[0:a][1:a]amix=inputs=2:normalize=0,aformat=channel_layouts=mono", out / "_mix.wav")
    mix_lufs, _ = loudness(out / "_mix.wav")
    gain = 0.0 if mix_lufs == float("-inf") else cfg["final_lufs"] - mix_lufs
    run("ffmpeg", "-v", "error", "-y", "-i", out / "_mix.wav", "-af",
        "volume=%.2fdB,alimiter=limit=0.891:level=false,aresample=48000" % gain, mix)
    (out / "_mix.wav").unlink()
    base_video = out / "picture_and_mix.mp4"
    run("ffmpeg", "-v", "error", "-y", "-i", staged, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", "-movflags", "+faststart", base_video)
    staged.unlink()
    rep["timeline_s"], rep["room_tone"] = round(total, 3), str(cfg["room_tone"])
    return rep, base_video, total


def transcript_words(out):
    run("npx", "-y", "hyperframes", "transcribe", "-m", "small.en", "--json", "voice_track.wav", cwd=out)
    words = json.loads((out / "transcript.json").read_text())
    return words if isinstance(words, list) else words["words"]


def caption_times(phrases, words):
    """Each phrase gets its words' times; any mismatch with the transcript fails (no guessing)."""
    toks = lambda s: re.findall(r"[a-z0-9']+", s.lower().replace("\u2019", "'"))
    out, i, bad = [], 0, []
    for p in phrases:
        n = len(toks(p))
        chunk = words[i:i + n]
        heard = [t for w in chunk for t in toks(w["text"])]
        if not chunk or heard != toks(p):
            bad.append("'%s' vs heard '%s'" % (p, " ".join(heard)))
            break
        out.append({"text": p, "start": chunk[0]["start"], "last_end": chunk[-1]["end"]})
        i += n
    if bad or i != len(words):
        raise Fail("captions don't match the voice transcript (%s); fix the phrases or turn captions off"
                   % (bad[0] if bad else "%d transcript words left over" % (len(words) - i)))
    return set_ends(out)


def set_ends(caps):
    """A caption runs to the next one when the gap is short, else 0.6 s past its last word."""
    for k, c in enumerate(caps):
        nxt = caps[k + 1]["start"] if k + 1 < len(caps) else None
        c["end"] = nxt if nxt is not None and nxt - c["last_end"] <= 0.6 else c["last_end"] + 0.6
    return caps


def silences(wav, noise_db=-70, min_s=0.12):
    """(start, end) of every near-silence in the voice-only track, from ffmpeg's silencedetect.

    -70 dB: only true pauses count. A higher threshold (-45 was tried) treats a quiet consonant at the
    start of a phrase (~-60 dBFS) as silence and would push a correct caption start late. A voice file
    with background noise above -70 dB shows no pauses, so its captions are simply left as transcribed.
    """
    err = run("ffmpeg", "-v", "info", "-i", wav, "-af", "silencedetect=noise=%ddB:d=%.2f" % (noise_db, min_s),
              "-f", "null", "-").stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    if len(ends) < len(starts):
        ends.append(duration(wav))
    return [(max(a, 0.0), b) for a, b in zip(starts, ends)]


def audible_end(wav):
    """End of the last audible sound in a voice file: where its trailing near-silence starts, else its end.

    Same -70 dB near-silence rule as caption timing. Anything louder counts as possible speech (a quiet
    final consonant can sit at -50 to -65 dBFS), so a file with noise or reverb after the last word has
    no trailing silence and any earlier trim is refused: it fails closed rather than guessing.
    """
    s, n = silences(wav), duration(wav)
    return s[-1][0] if s and s[-1][1] >= n - 0.02 else n


def audible_start(wav):
    """Start of the first audible sound in a voice file: where its leading near-silence ends, else 0.

    The mirror of audible_end, with the same -70 dB rule, so a start trim fails closed on a noisy head.
    """
    s = silences(wav)
    return s[0][1] if s and s[0][0] <= 0.02 else 0.0


def align_starts(caps, silent):
    """Move a caption's start to the real speech onset when the transcriber placed it inside a silence.

    The transcriber's first-word start often absorbs the pause before it, so the caption would show
    while nothing is said. Only starts that fall inside a detected silence move, and only to that
    silence's end (the measured onset), never past the phrase's last word. Others stay as they are.
    """
    moved = []
    for c in caps:
        for a, b in silent:
            # within 50 ms of the detected onset counts as aligned (silencedetect reports onsets ~20 ms late)
            if a - 1e-3 <= c["start"] < b - 0.05 and b < c["last_end"]:
                moved.append({"text": c["text"], "from": c["start"], "to": round(b, 3)})
                c["start"] = round(b, 3)
                break
    set_ends(caps)
    return moved


def burn_captions(out, base_video, mix, caps, cfg, total, final):
    proj = out / "captions"
    proj.mkdir()
    os.link(base_video, proj / "source.mp4")
    c = cfg["captions"]
    w, h = cfg["size"]
    box = (" .cap span { background: rgba(0,0,0,.42); padding: 6px 22px 10px; border-radius: 18px;"
           " -webkit-box-decoration-break: clone; box-decoration-break: clone; }") if c.get("box", True) else ""
    zone = lambda t: next((z[2] for z in c.get("zones") or [] if z[0] <= t < z[1]), None)
    divs = "\n".join('  <div class="clip cap" id="c%d"%s data-start="%.3f" data-duration="%.3f" data-track-index="1"><span>%s</span></div>'
                     % (k, ' style="top: %dpx"' % zone(x["start"]) if zone(x["start"]) is not None else "", x["start"],
                        min(x["end"], total) - x["start"], html.escape(x["text"])) for k, x in enumerate(caps))
    fades = "\n".join('  tl.fromTo("#c%d", {opacity: 0, y: 8}, {opacity: 1, y: 0, duration: 0.08}, %.3f);' % (k, x["start"])
                      for k, x in enumerate(caps))
    (proj / "index.html").write_text("""<!doctype html>
<html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
  body { margin: 0; background: #000; }
  #root { position: relative; width: %dpx; height: %dpx; overflow: hidden; }
  #bg { position: absolute; inset: 0; width: %dpx; height: %dpx; object-fit: cover; }
  .cap { position: absolute; left: 90px; right: 90px; top: %dpx; text-align: center; color: #fff;
         font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-weight: 800; font-size: 66px;
         line-height: 1.15; text-shadow: 0 3px 10px rgba(0,0,0,.75), 0 0 2px rgba(0,0,0,.9); }%s
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-width="%d" data-height="%d" data-duration="%.3f">
  <video id="bg" src="source.mp4" data-start="0" data-duration="%.3f" data-media-start="0" data-track-index="0" muted playsinline></video>
%s
</div>
<script>
(() => {
  const tl = gsap.timeline({ paused: true });
%s
  window.__timelines = window.__timelines || {};
  window.__timelines["main"] = tl;
})();
</script>
</body></html>
""" % (w, h, w, h, int(c.get("top", 960)), box, w, h, total, total, divs, fades))
    (proj / "captions.json").write_text(json.dumps(caps, indent=2))
    raw = out / "_captioned.mp4"
    run("npx", "-y", "hyperframes", "render", proj, "-o", raw, "-f", str(cfg["fps"]), "--quiet")
    run("ffmpeg", "-v", "error", "-y", "-i", raw, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", "-movflags", "+faststart", final)
    raw.unlink()


def checks(out, final, rep, used, words, caps, total, rc):
    lufs, tp = loudness(final)
    rep["final"] = {"file": str(final), "lufs": round(lufs, 1), "true_peak": round(tp, 1),
                    "video_s": round(duration(final, "v:0"), 3), "audio_s": round(duration(final, "a:0"), 3)}
    rep["room_tone_rms_first_last_db"] = [round(rms_db(out / "bed_track.wav", 1, 2), 1),
                                          round(rms_db(out / "bed_track.wav", total - 3, 2), 1)]
    shots = out / "_shots"
    shots.mkdir()
    for j in rep["joins"]:
        a, b = shots / "a.png", shots / "b.png"
        run("ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % (j["at"] - 0.05), "-i", final, "-frames:v", "1", a)
        run("ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % (j["at"] + 0.01), "-i", final, "-frames:v", "1", b)
        j["frame_ssim"] = rc.ssim(str(a), str(b), (360, 640))
        rc.side_by_side(str(a), str(b), str(out / ("join_%05.2f.png" % j["at"])), (360, 640))
    if words is not None:
        rep["dialogue"] = rc.compare_words(" ".join(u["dialogue"] for u in used if u["dialogue"]), words)
    sheets = []
    if caps:
        rep["captions"] = {"count": len(caps), "first_start": caps[0]["start"], "last_end": round(caps[-1]["end"], 3)}
        sheets.append(("contact_captions.png", [(c["start"] + c["end"]) / 2 for c in caps], 6))
    sheets.append(("contact_ending.png", [max(total - 3.5 + 0.5 * k, 0) for k in range(7)] + [max(total - 0.1, 0)], 8))
    for name, times, cols in sheets:
        for k, t in enumerate(times):
            run("ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % t, "-i", final, "-frames:v", "1", "-vf", "scale=270:-2",
                shots / ("f%03d.png" % k))
        rows = (len(times) + cols - 1) // cols
        run("ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", shots / "f%03d.png", "-vf",
            "tile=%dx%d:padding=4:color=white" % (cols, rows), "-frames:v", "1", out / name)
        for f in shots.glob("f*.png"):
            f.unlink()
    for f in shots.iterdir():
        f.unlink()
    shots.rmdir()


def main():
    ap = argparse.ArgumentParser(description="Assemble approved clips from a flow-video storyboard (read-only on inputs).")
    ap.add_argument("storyboard")
    ap.add_argument("--out", required=True, help="new folder for this render; must not exist yet")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--guard", default=str(Path("~/.claude/hooks/flow_guard.py").expanduser()))
    a = ap.parse_args()
    sb_path = Path(a.storyboard).expanduser().resolve()
    out = Path(a.out).expanduser().resolve()
    try:
        sb = json.loads(sb_path.read_text())
    except Exception as e:
        sys.exit("ERROR cannot read storyboard %s (%s)" % (sb_path, e))
    base = sb_path.parent
    chk = module("storyboard_check").Check(sb, base, a.guard).run()
    if chk.errors:
        sys.exit("ERROR storyboard_check found %d error(s); fix them before assembling:\n  %s"
                 % (len(chk.errors), "\n  ".join(chk.errors)))
    if out.exists():
        sys.exit("ERROR output folder already exists (%s); choose a new --out so nothing is overwritten" % out)
    cfg = settings(sb)
    cap = cfg["captions"]
    try:
        if cap.get("enabled") and (a.no_transcribe or not cap.get("phrases")):
            raise Fail("captions are enabled but need %s" % ("transcription (drop --no-transcribe)" if a.no_transcribe
                                                             else "assembly.captions.phrases"))
        for k in ("end_hold", "end_fade"):
            if not isinstance(cfg[k], (int, float)) or cfg[k] < 0:
                raise Fail("assembly.%s must be a number of seconds >= 0" % k)
        used = plan(sb, base, lambda p: (lambda q: q if q.is_absolute() else base / q)(Path(os.path.expanduser(p))))
    except Fail as e:
        sys.exit("ERROR " + str(e).replace("\n", "\nERROR "))
    if not cfg["room_tone"]:
        print("WARN  no assembly.room_tone set: the video will have no room tone under the voice")
    out.mkdir(parents=True)
    rc = module("review_clip")
    rep, base_video, total = render(sb, base, out, cfg, used)
    words = None if a.no_transcribe else transcript_words(out)
    final = out / ("%s-assembled.mp4" % (sb.get("project") or "video"))
    caps = None
    try:
        if cap.get("enabled"):
            caps = caption_times(cap["phrases"], words)
            rep["caption_start_corrections"] = align_starts(caps, silences(out / "voice_track.wav"))
    except Fail as e:
        sys.exit("ERROR %s (partial files left in %s)" % (e, out))
    if caps:
        burn_captions(out, base_video, out / "mix.wav", caps, cfg, total, final)
    else:
        os.link(base_video, final)
    checks(out, final, rep, used, words, caps, total, rc)
    (out / "report.json").write_text(json.dumps(rep, indent=2))
    print("wrote %s\n%s" % (final, json.dumps({k: rep[k] for k in ("final", "joins", "lines") if k in rep}, indent=1)))


if __name__ == "__main__":
    main()
