"""Offline tests for scripts/assemble.py. No Flow calls; every output goes to a temp folder.

Run (from the studio folder):
  cd ~/"Claude Code"/video-studio && python3 -I scripts/tests/test_assemble.py
Golden regression against the Heating Tray v3 preview (reads its files only; takes a few minutes):
  cd ~/"Claude Code"/video-studio && RUN_GOLDEN=1 python3 -I scripts/tests/test_assemble.py Golden
The v3 clip-13 trim cuts speech; the golden tests prove it is refused and compare the rest (see Golden).
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
ASSEMBLE = SCRIPTS / "assemble.py"
GUARD_SRC = ('OMNI_CREDITS = {"360p": {4: 4, 6: 5, 8: 6, 10: 7}, "720p": {4: 7, 6: 10, 8: 12, 10: 15}}\n'
             'VIDEO_MODELS = {"Omni 1.1 Flash": OMNI_CREDITS}\n')
OMNI = {"durations_seconds": [4, 6, 8, 10], "resolutions": ["360p", "720p"], "prices_approved_by_user": True,
        "credits": {"360p": {"4": 4, "6": 5, "8": 6, "10": 7}, "720p": {"4": 7, "6": 10, "8": 12, "10": 15}},
        "credits_source": "test", "verified": {}}


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def storyboard(scenes, keyframes, assembly, cap):
    ok = {"status": "approved", "approved_at": "test", "note": ""}
    return {"schema": "flow-video/1", "project": "test", "flow_project_folder": "Test",
            "format": {"aspect_ratio": "9:16", "final_resolution": "1080x1920"},
            "budget": {"credit_cap_total": cap, "credits_spent": 0, "ledger": []},
            "approvals": {"storyboard": dict(ok), "keyframes": dict(ok), "voice": dict(ok)},
            "default_video_model": "Omni 1.1 Flash", "model_profiles": {"Omni 1.1 Flash": copy.deepcopy(OMNI)},
            "voice": {"engine": "gemini", "voice": "Aoede", "style_prompt": "warm, relaxed"},
            "continuity": {"character": {"lock_text": "c"}, "product": {"lock_text": "p"}, "environment": {"lock_text": "e"},
                           "camera": "c", "lighting": "l", "negative": "n"},
            "keyframes": [{"id": k, "file": str(f), "status": "approved"} for k, f in keyframes],
            "scenes": scenes, "assembly": assembly}


def scene(sid, order, k0, k1, clip, dialogue="", tts=None, tts_s=None, dur=4, credits=7, **extra):
    s = {"id": sid, "order": order, "beat": sid, "shot_type": "talking" if dialogue else "cutaway",
         "dialogue": dialogue, "tts_file": str(tts) if tts else None, "tts_seconds": tts_s,
         "tts_status": "approved" if dialogue else "pending", "start_keyframe": k0, "end_keyframe": k1,
         "prompt": "p", "video": {"model": "Omni 1.1 Flash", "aspect_ratio": "9:16", "duration_seconds": dur,
                                  "resolution": "720p", "expected_credits": credits, "max_credits": credits,
                                  "variants": 1, "retries": 0},
         "generation": {"status": "downloaded", "clip_file": str(clip)}, "review": {"status": "approved"}}
    s.update(extra)
    return s


class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.dir = Path(cls.tmp.name)
        for i, (pat, audio) in enumerate([("testsrc2", True), ("smptebars", False), ("rgbtestsrc", False)]):
            args = ["-f", "lavfi", "-i", "%s=s=720x1280:r=24:d=4" % pat]
            if audio:
                args += ["-f", "lavfi", "-i", "sine=f=500:d=4"]
            ff(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", d / ("clip%d.mp4" % i))
            ff("-i", d / ("clip%d.mp4" % i), "-frames:v", "1", d / ("K%d.png" % i))
        ff("-sseof", "-0.1", "-i", d / "clip2.mp4", "-frames:v", "1", d / "K3.png")
        ff("-f", "lavfi", "-i", "sine=f=220:d=2.5", "-af", "apad=whole_dur=4", "-ar", "48000", "-ac", "1", d / "v1.wav")
        ff("-f", "lavfi", "-i", "sine=f=260:d=3.0", "-af", "apad=whole_dur=4", "-ar", "48000", "-ac", "1", d / "v3.wav")
        ff("-f", "lavfi", "-i", "anoisesrc=color=pink:d=6:a=0.05", "-ar", "48000", "-ac", "1", d / "room.wav")
        (d / "guard.py").write_text(GUARD_SRC)
        cls.inputs = sorted(p for p in d.iterdir() if p.is_file())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sb(self):
        d = self.dir
        kfs = [("K%d" % i, d / ("K%d.png" % i)) for i in range(4)]
        scenes = [scene("S01", 1, "K0", "K1", d / "clip0.mp4", "One two three.", d / "v1.wav", 2.5),
                  scene("S02", 2, "K1", "K2", d / "clip1.mp4"),
                  scene("S03", 3, "K2", "K3", d / "clip2.mp4", "Four five six.", d / "v3.wav", 3.0)]
        return storyboard(scenes, kfs, {"room_tone": str(d / "room.wav"), "end_hold": 1.0, "end_fade": 0.6}, 30)

    def run_assemble(self, sb, out_name, *flags):
        work = Path(tempfile.mkdtemp(dir=self.dir))
        (work / "storyboard.json").write_text(json.dumps(sb))
        out = work / out_name
        r = subprocess.run([sys.executable, "-I", str(ASSEMBLE), str(work / "storyboard.json"), "--out", str(out),
                            "--guard", str(self.dir / "guard.py"), *flags], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr, out

    def refused(self, sb, needle, *flags):
        code, msg, out = self.run_assemble(sb, "render", "--no-transcribe", *flags)
        self.assertNotEqual(code, 0, msg)
        self.assertIn(needle, msg)
        self.assertFalse(out.exists(), "nothing may be written when validation fails")

    def test_happy_path_and_sources_untouched(self):
        before = {p: sha(p) for p in self.inputs}
        code, msg, out = self.run_assemble(self.sb(), "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)
        rep = json.loads((out / "report.json").read_text())
        final = Path(rep["final"]["file"])
        self.assertTrue(final.is_file())
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", str(final)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(size, "1080,1920")
        self.assertAlmostEqual(rep["timeline_s"], 13.0, delta=0.01)
        self.assertAlmostEqual(rep["final"]["video_s"], 13.0, delta=0.06)
        self.assertAlmostEqual(rep["final"]["audio_s"], 13.0, delta=0.06)
        self.assertAlmostEqual(rep["final"]["lufs"], -14.0, delta=0.6)
        self.assertEqual(sorted(rep["lines"]), ["S01", "S03"])
        for line in rep["lines"].values():
            self.assertAlmostEqual(line["lufs_after"], -20.0, delta=0.3)
        self.assertEqual([j["into"] for j in rep["joins"]], ["S02", "S03"])
        self.assertTrue(all(isinstance(j["frame_ssim"], float) for j in rep["joins"]))
        first, last = rep["room_tone_rms_first_last_db"]
        self.assertLess(abs(first - last), 3.0, "room tone must stay steady (it loops when shorter than the video)")
        err = subprocess.run(["ffmpeg", "-v", "info", "-ss", "12.9", "-i", str(final), "-frames:v", "1", "-vf",
                              "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        self.assertLess(float(re.search(r"YAVG=([0-9.]+)", err).group(1)), 40, "the ending must fade to black")
        self.assertTrue((out / "contact_ending.png").is_file())
        self.assertFalse(list(out.glob("_*")), "no temporary files left behind")
        self.assertEqual(before, {p: sha(p) for p in self.inputs}, "source files must not change")

    def test_refuses_existing_output_folder(self):
        work = Path(tempfile.mkdtemp(dir=self.dir))
        (work / "render").mkdir()
        (work / "storyboard.json").write_text(json.dumps(self.sb()))
        r = subprocess.run([sys.executable, "-I", str(ASSEMBLE), str(work / "storyboard.json"), "--out", str(work / "render"),
                            "--guard", str(self.dir / "guard.py"), "--no-transcribe"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(list((work / "render").iterdir()), [])

    def test_refuses_unreviewed_scene(self):
        sb = self.sb(); sb["scenes"][2]["review"]["status"] = "pending"
        self.refused(sb, "scene S03 is not reviewed")

    def test_refuses_missing_clip(self):
        sb = self.sb(); sb["scenes"][1]["generation"]["clip_file"] = str(self.dir / "nope.mp4")
        self.refused(sb, "clip file missing")

    def test_refuses_missing_voice(self):
        sb = self.sb(); sb["scenes"][0]["tts_file"] = str(self.dir / "nope.wav")
        self.refused(sb, "approved TTS file missing")  # caught by storyboard_check first

    def test_trim_at_matching_join_needs_approved_cut(self):
        sb = self.sb(); sb["scenes"][0]["trim_end"] = 3.0
        self.refused(sb, "join S01 -> S02 no longer matches")
        sb["scenes"][1]["join_previous"] = "cut"
        code, msg, _ = self.run_assemble(sb, "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)

    def test_trim_that_cuts_speech_is_refused(self):
        sb = self.sb(); sb["scenes"][2]["trim_end"] = 2.0  # the voice speaks until 3.0 s
        self.refused(sb, "the trim would cut speech")

    def test_safe_trim_after_speech_is_allowed(self):
        sb = self.sb(); sb["scenes"][2]["trim_end"] = 3.6  # last scene; speech ends at 3.0 s, silence after
        code, msg, out = self.run_assemble(sb, "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)
        rep = json.loads((out / "report.json").read_text())
        self.assertAlmostEqual(rep["timeline_s"], 4 + 4 + 3.6 + 1.0, delta=0.01)

    def test_start_trim_at_matching_join_needs_approved_cut(self):
        sb = self.sb(); sb["scenes"][1]["trim_start"] = 1.0  # S02 has no voice
        self.refused(sb, "join S01 -> S02 no longer matches")
        sb["scenes"][1]["join_previous"] = "cut"
        code, msg, out = self.run_assemble(sb, "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)
        rep = json.loads((out / "report.json").read_text())
        self.assertAlmostEqual(rep["timeline_s"], 4 + 3 + 4 + 1.0, delta=0.01)

    def test_start_trim_that_cuts_speech_is_refused(self):
        sb = self.sb(); sb["scenes"][2]["trim_start"] = 1.0; sb["scenes"][2]["join_previous"] = "cut"  # speech from 0 s
        self.refused(sb, "the start trim would cut speech")

    def test_start_trim_before_speech_is_allowed(self):
        lead = Path(tempfile.mkdtemp(dir=self.dir)) / "lead.wav"
        ff("-f", "lavfi", "-i", "sine=f=260:d=2.0", "-af", "adelay=1500|1500,apad=whole_dur=4", "-ar", "48000", "-ac", "1", lead)
        sb = self.sb(); s = sb["scenes"][2]
        s["tts_file"] = str(lead); s["trim_start"] = 1.2; s["join_previous"] = "cut"
        code, msg, out = self.run_assemble(sb, "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)
        rep = json.loads((out / "report.json").read_text())
        self.assertAlmostEqual(rep["timeline_s"], 4 + 4 + 2.8 + 1.0, delta=0.01)

    def test_grade_changes_colour_not_timing(self):
        sb = self.sb(); sb["scenes"][1]["grade"] = "eq=brightness=-0.5"  # S02 runs 4.0-8.0 s on the timeline
        code, msg, out = self.run_assemble(sb, "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)
        rep = json.loads((out / "report.json").read_text())
        self.assertAlmostEqual(rep["timeline_s"], 13.0, delta=0.01)
        final = rep["final"]["file"]
        def yavg(t):
            err = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(t), "-i", final, "-frames:v", "1", "-vf",
                                  "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                                 capture_output=True, text=True).stderr
            return float(re.search(r"YAVG=([0-9.]+)", err).group(1))
        self.assertLess(yavg(6.0), 40, "the graded scene must be darker")
        self.assertGreater(yavg(2.0), 60, "other scenes stay ungraded")

    def test_grade_rejects_filtergraph_syntax(self):
        sb = self.sb(); sb["scenes"][1]["grade"] = "eq=brightness=0[x];[x]null"
        self.refused(sb, "grade must be a single ffmpeg filter chain")

    def test_start_trim_must_leave_half_a_second(self):
        sb = self.sb(); sb["scenes"][1]["trim_start"] = 3.8; sb["scenes"][1]["join_previous"] = "cut"
        self.refused(sb, "trim_start 3.8 must be between")

    def make_voice(self, name, segments):
        """32-bit float voice from (kind, seconds, dBFS) segments; kind is tone, noise or silence. Levels are measured."""
        f = "aformat=sample_fmts=flt:channel_layouts=mono"
        args, chains, t = [], [], 0.0
        for i, (kind, dur, dbfs) in enumerate(segments):
            src = {"tone": "sine=f=300:r=48000:d=%s" % dur, "noise": "anoisesrc=color=white:r=48000:a=0.5:d=%s" % dur,
                   "silence": "anullsrc=r=48000:cl=mono:d=%s" % dur}[kind]
            base = {"tone": -21.07, "noise": -10.79, "silence": 0}[kind]  # measured RMS of each source at 0 dB
            args += ["-f", "lavfi", "-i", src]
            chains.append("[%d:a]%s%s[s%d]" % (i, "volume=%.2fdB," % (dbfs - base) if kind != "silence" else "", f, i))
        path = self.dir / name
        ff(*args, "-filter_complex", ";".join(chains) + ";" + "".join("[s%d]" % i for i in range(len(segments)))
           + "concat=n=%d:v=0:a=1" % len(segments), "-c:a", "pcm_f32le", path)
        for kind, dur, dbfs in segments:
            if kind != "silence":
                err = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "atrim=%s:%s,astats=measure_overall="
                                      "RMS_level:measure_perchannel=none" % (t + 0.01, t + dur - 0.01), "-f", "null", "-"],
                                     capture_output=True, text=True).stderr
                self.assertAlmostEqual(float(re.findall(r"RMS level dB:\s+(-?[\d.]+)", err)[-1]), dbfs, delta=1.0,
                                       msg="fixture segment is not at its intended level")
            t += dur
        return path

    def last_scene_trim(self, voice, trim):
        sb = self.sb()
        sb["scenes"][2]["tts_file"] = str(voice)
        sb["scenes"][2]["trim_end"] = trim
        return sb

    def test_quiet_final_consonant_is_protected(self):
        v = self.make_voice("quiet_end.wav", [("tone", 2.8, -21), ("tone", 0.2, -60), ("silence", 1.0, 0)])
        self.refused(self.last_scene_trim(v, 2.9), "the trim would cut speech")  # the old -45 dB rule allowed this

    def test_trim_after_quiet_final_consonant_is_allowed(self):
        v = self.make_voice("quiet_end_ok.wav", [("tone", 2.8, -21), ("tone", 0.2, -60), ("silence", 1.0, 0)])
        code, msg, _ = self.run_assemble(self.last_scene_trim(v, 3.05), "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)

    def test_near_silence_floor_counts_as_silence(self):
        v = self.make_voice("floor.wav", [("tone", 3.0, -21), ("tone", 1.0, -80)])
        code, msg, _ = self.run_assemble(self.last_scene_trim(v, 3.1), "render", "--no-transcribe")
        self.assertEqual(code, 0, msg)

    def test_short_remnant_in_long_file_is_refused(self):
        v = self.make_voice("long.wav", [("tone", 3.0, -21), ("silence", 7.0, 0)])  # averaging the 7 s tail hid this
        self.refused(self.last_scene_trim(v, 2.97), "the trim would cut speech")

    def test_noise_after_speech_fails_closed(self):
        v = self.make_voice("noise_tail.wav", [("tone", 3.0, -21), ("noise", 1.0, -60)])
        self.refused(self.last_scene_trim(v, 3.5), "audible until 4.00 s")

    def test_rejected_scene_needs_cut(self):
        sb = self.sb(); sb["scenes"][1]["review"]["status"] = "rejected"
        self.refused(sb, "a scene between them was rejected")

    def test_captions_need_transcription(self):
        sb = self.sb(); sb["assembly"]["captions"] = {"enabled": True, "phrases": ["One two three."]}
        self.refused(sb, "captions are enabled but need transcription")

    def test_storyboard_errors_block_assembly(self):
        sb = self.sb(); sb["model_profiles"]["Omni 1.1 Flash"]["prices_approved_by_user"] = False
        self.refused(sb, "storyboard_check found")

    def test_caption_mismatch_fails_closed(self):
        sys.path.insert(0, str(SCRIPTS))
        import assemble
        words = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, t in enumerate("one two three".split())]
        self.assertEqual(len(assemble.caption_times(["one two", "three"], words)), 2)
        with self.assertRaises(assemble.Fail):
            assemble.caption_times(["one two", "four"], words)
        with self.assertRaises(assemble.Fail):
            assemble.caption_times(["one two"], words)  # leftover word


class CaptionTiming(unittest.TestCase):
    """Caption starts the transcriber put inside a pause move to the measured speech onset; others stay."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS))
        import assemble
        cls.asm = assemble
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wav = Path(cls.tmp.name) / "voice.wav"
        # speech (tone) at 0.8-1.8 s, 2.7-3.7 s and 3.75-4.55 s: leading silence 0.8 s, a 0.9 s pause,
        # and a 0.05 s breath gap that is too short to count as a pause
        ff("-f", "lavfi", "-i", "sine=f=300:d=4.55", "-af",
           "volume=enable='between(t,0,0.8)+between(t,1.8,2.7)+between(t,3.7,3.75)':volume=0",
           "-ar", "48000", "-ac", "1", cls.wav)
        cls.silent = assemble.silences(cls.wav)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def caps(self, starts):
        words = [{"text": t, "start": s, "end": e} for t, (s, e) in zip(("alpha", "bravo", "charlie"), starts)]
        return self.asm.caption_times(["alpha", "bravo", "charlie"], words)

    def test_detects_only_real_pauses(self):
        self.assertEqual(len(self.silent), 2, self.silent)
        (a0, a1), (b0, b1) = self.silent
        self.assertAlmostEqual(a0, 0.0, delta=0.02); self.assertAlmostEqual(a1, 0.8, delta=0.03)
        self.assertAlmostEqual(b0, 1.8, delta=0.03); self.assertAlmostEqual(b1, 2.7, delta=0.03)

    def test_leading_silence_and_pause_move_to_onset(self):
        caps = self.caps([(0.0, 1.8), (1.9, 3.7), (3.75, 4.55)])  # transcriber absorbed both silences
        moved = self.asm.align_starts(caps, self.silent)
        self.assertEqual([m["text"] for m in moved], ["alpha", "bravo"])
        self.assertAlmostEqual(caps[0]["start"], 0.8, delta=0.03)
        self.assertAlmostEqual(caps[1]["start"], 2.7, delta=0.03)
        self.assertEqual(caps[2]["start"], 3.75, "a start after a short breath gap must not move")
        self.assertAlmostEqual(caps[0]["end"], 1.8 + 0.6, delta=0.001)  # long pause: ends 0.6 s after its last word
        self.assertEqual(caps[1]["end"], caps[2]["start"])            # short gap: runs to the next caption
        self.assertEqual([c["text"] for c in caps], ["alpha", "bravo", "charlie"])

    def test_correct_times_are_left_alone(self):
        caps = self.caps([(0.8, 1.8), (2.7, 3.7), (3.75, 4.55)])
        self.assertEqual(self.asm.align_starts(caps, self.silent), [])
        self.assertEqual([c["start"] for c in caps], [0.8, 2.7, 3.75])

    def test_continuous_speech_is_unchanged(self):
        caps = self.caps([(0.8, 1.2), (1.2, 1.5), (1.5, 1.8)])  # all inside the first spoken stretch
        self.assertEqual(self.asm.align_starts(caps, self.silent), [])
        self.assertEqual([c["start"] for c in caps], [0.8, 1.2, 1.5])

    def quiet_onset_wav(self, consonant_dbfs):
        """0.8 s silence, a 0.15 s quiet consonant at the given level, 0.85 s of normal speech, 0.5 s silence."""
        wav = Path(self.tmp.name) / ("quiet_%d.wav" % abs(consonant_dbfs))
        # a full-scale-1/8 sine is about -21 dBFS RMS; scale it to the requested consonant level. Everything is
        # forced to 32-bit float: left to itself concat picked 8-bit audio, whose floor (~-48 dBFS) erased the consonant.
        f = "aformat=sample_fmts=flt:channel_layouts=mono"
        ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.8", "-f", "lavfi", "-i", "sine=f=4000:d=0.15:r=48000",
           "-f", "lavfi", "-i", "sine=f=300:d=0.85:r=48000", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=0.5",
           "-filter_complex", "[0:a]%s[a];[1:a]volume=%.1fdB,%s[b];[2:a]%s[c];[3:a]%s[d];[a][b][c][d]concat=n=4:v=0:a=1"
           % (f, consonant_dbfs + 21, f, f, f), "-c:a", "pcm_f32le", wav)
        level = subprocess.run(["ffmpeg", "-v", "info", "-i", str(wav), "-af", "atrim=0.8:0.95,astats=measure_overall="
                                "RMS_level:measure_perchannel=none", "-f", "null", "-"], capture_output=True, text=True).stderr
        self.assertAlmostEqual(float(re.findall(r"RMS level dB:\s+(-?[\d.]+)", level)[-1]), consonant_dbfs, delta=0.5,
                               msg="fixture consonant is not at the intended level")
        return wav

    def test_quiet_consonant_onset_does_not_jump_forward(self):
        for level in (-40, -50, -60, -65):  # at the old -45 dB threshold, -50/-60/-65 all moved 0.15 s late
            with self.subTest(consonant_dbfs=level):
                silent = self.asm.silences(self.quiet_onset_wav(level))
                words = [{"text": "hello", "start": 0.8, "end": 1.8}]  # the transcriber got the onset right
                caps = self.asm.caption_times(["hello"], words)
                moved = self.asm.align_starts(caps, silent)
                self.assertEqual(moved, [], "a correct start before a quiet consonant moved: %s (silences %s)" % (moved, silent))
                self.assertEqual(caps[0]["start"], 0.8)

    def test_never_moves_past_the_phrase(self):
        caps = self.caps([(0.1, 0.5), (2.7, 3.7), (3.75, 4.55)])  # phrase ends inside the silence: leave it
        self.assertEqual(self.asm.align_starts(caps, self.silent), [])
        self.assertEqual(caps[0]["start"], 0.1)


HT = Path("~/Claude Code/video-studio/projects/heating-tray").expanduser()
CLIPS = Path("~/flow-mcp-out/HeatingTray").expanduser()
VOICE = Path("~/Downloads/Heating-Tray-Voiceover/lip-synced").expanduser()
ROOM = Path("~/Downloads/Heating-Tray-Background-Sound/background-family-chatter-45s-loop.wav").expanduser()
V3 = HT / "work" / "preview-v3"


@unittest.skipUnless(os.environ.get("RUN_GOLDEN") == "1" and (V3 / "report.json").exists(), "set RUN_GOLDEN=1 (needs Heating Tray v3)")
class Golden(unittest.TestCase):
    """Historical comparison with the hand-built Heating Tray v3 preview, kept apart from correctness.

    KNOWN v3 DISCREPANCY: v3 trims clip 13 at 7.875 s, which cuts the final 0.125 s of "everyone."
    (voice tail ~-38 dB RMS, peak ~-27 dB). The assembler correctly refuses that trim, so:
      - test_v3_clip13_trim_is_refused proves the refusal (it is not waived);
      - test_matches_v3_without_clip13_trim keeps clip 13 whole and checks everything the change
        doesn't affect, with times after the clip-13 join shifted by the extra 0.125 s.
    """
    IDS = ["02", "03", "13", "16", "18"]
    V3_TRIMS = [None, None, 7.875, 7.5833, 6.3333]
    SHIFT_AT, SHIFT = 23.875, 0.125  # v3's clip-13 join, and the time clip 13 keeps when not trimmed
    LINES = [  # this fixture's dialogue (test data only)
        "If you host during the holidays, this is literally how I keep all the food warm while everyone's still here.",
        "I just leave these out with the food while everyone's here. And normally by then, half the stuff sitting out is already cold.",
        "So this way I'm not constantly taking stuff back into the kitchen just to warm it up again. I can actually just stay out here with everyone.",
        "And when we're done, they come apart and stack up, so they're actually really easy to put away.",
        "If you're the one hosting everybody this holiday, you need to pick one up. I linked it below."]

    def build(self, tmp, trims):
        frames = [CLIPS / ("scene-%s-lastframe.jpg" % i) for i in ("02", "03", "04", "10")]
        kfs = [("K%d" % k, frames[k % 4]) for k in range(6)]
        scenes = []
        for n, (cid, line, trim) in enumerate(zip(self.IDS, self.LINES, trims)):
            extra = {"trim_end": trim} if trim else {}
            if n in (3, 4):
                extra["join_previous"] = "cut"  # v3's hard cuts after clips 13 and 16
            # pre-production numbers are placeholders that satisfy the checker; only assembly is under test
            scenes.append(scene("S0%d" % (n + 1), n + 1, "K%d" % n, "K%d" % (n + 1), CLIPS / ("scene-%s.mp4" % cid),
                                line, VOICE / ("line%d-synced.wav" % (n + 1)), 7.0, dur=8, credits=12, **extra))
        phrases = [p.strip() for p in (HT / "src" / "captions.txt").read_text().splitlines() if p.strip()]
        sb = storyboard(scenes, kfs, {"room_tone": str(ROOM), "end_hold": 1.2, "end_fade": 0.7,
                                      "captions": {"enabled": True, "phrases": phrases, "top": 960,
                                                   "zones": [[23.875, 36.2, 640]]}}, 100)
        (tmp / "guard.py").write_text(GUARD_SRC)
        (tmp / "storyboard.json").write_text(json.dumps(sb))
        return subprocess.run([sys.executable, "-I", str(ASSEMBLE), str(tmp / "storyboard.json"), "--out", str(tmp / "render"),
                               "--guard", str(tmp / "guard.py")], capture_output=True, text=True)

    def watched(self):
        return ([CLIPS / ("scene-%s.mp4" % i) for i in self.IDS] + sorted(VOICE.glob("*.wav"))
                + [ROOM, V3 / "heating-tray-preview-v3-captions.mp4", V3 / "report.json"])

    def shifted(self, t):
        return t + self.SHIFT if t >= self.SHIFT_AT - 1e-6 else t

    def test_v3_clip13_trim_is_refused(self):
        before = {p: sha(p) for p in self.watched()}
        with tempfile.TemporaryDirectory() as tmp:
            r = self.build(Path(tmp), self.V3_TRIMS)
            self.assertNotEqual(r.returncode, 0, "v3's clip-13 trim cuts speech and must be refused")
            self.assertIn("scene S03: the voice line runs past the kept clip", r.stderr)
            self.assertIn("the trim would cut speech", r.stderr)
            self.assertFalse((Path(tmp) / "render").exists())
        print("\nKNOWN v3 DISCREPANCY (not waived): v3 cuts the final 0.125 s of \"everyone.\" at the clip-13 join; "
              "the assembler refuses that trim.", file=sys.stderr)
        self.assertEqual(before, {p: sha(p) for p in self.watched()})

    def test_matches_v3_without_clip13_trim(self):
        trims = list(self.V3_TRIMS)
        trims[2] = None  # keep clip 13 whole: its voice is no longer cut
        before = {p: sha(p) for p in self.watched()}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            r = self.build(tmp, trims)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.loads((tmp / "render" / "report.json").read_text())
            v3 = json.loads((V3 / "report.json").read_text())
            v3caps = json.loads((V3 / "captions" / "captions.json").read_text())
            # timeline and joins: identical except the expected +0.125 s after the clip-13 join
            self.assertAlmostEqual(rep["timeline_s"], v3["final"]["timeline_s"] + self.SHIFT, delta=0.01)
            self.assertEqual([j["at"] for j in rep["joins"]], [round(self.shifted(j["at"]), 3) for j in v3["joins"]])
            for a, b in zip(rep["joins"], v3["joins"]):
                if abs(b["at"] - self.SHIFT_AT) < 1e-3:
                    # affected by the change: v3's trim also lined this join up visually. Report, don't compare.
                    print("\nKNOWN TRADE-OFF: keeping clip 13 whole makes the clip-13 -> clip-16 join a visible jump "
                          "(frame similarity %.3f vs v3 %.3f). Neither option is clean; this needs a decision." %
                          (a["frame_ssim"], b["frame_ssim"]), file=sys.stderr)
                    continue
                self.assertAlmostEqual(a["frame_ssim"], b["frame_ssim"], delta=0.02)
            # audio treatment: same per-line loudness, final loudness, full dialogue heard
            for (k, line), cid in zip(sorted(rep["lines"].items()), self.IDS):
                self.assertAlmostEqual(line["lufs_after"], v3["lines"][cid]["lufs_after"], delta=0.1)
            self.assertAlmostEqual(rep["final"]["lufs"], v3["final"]["lufs"], delta=0.2)
            self.assertEqual(rep["dialogue"]["word_match"], 1.0)
            # captions: same phrases; times equal before the join, shifted by 0.125 s after it
            caps = json.loads((tmp / "render" / "captions" / "captions.json").read_text())
            self.assertEqual([c["text"] for c in caps], [c["text"] for c in v3caps])
            # Caption times come from the transcriber, whose word starts absorb preceding silence (v3 shows
            # "I just leave these out" at 6.94 s; the voice is silent 7.0-7.75 s and starts at ~8.0 s) and move
            # when later audio changes. They are not a reliable regression oracle: report, don't assert.
            # correctness (independent of v3): no caption may start inside a pause of the real voice track
            sys.path.insert(0, str(SCRIPTS))
            import assemble
            silent = assemble.silences(tmp / "render" / "voice_track.wav")
            early = [c["text"] for c in caps for a, b in silent if a - 1e-3 <= c["start"] < b - 0.05 and b < c["last_end"]]
            self.assertEqual(early, [], "captions still start inside a pause")
            fixes = rep.get("caption_start_corrections", [])
            print("\nCAPTION START CORRECTIONS: %d moved to the measured speech onset%s" % (
                len(fixes), "".join("\n  %-28s %.2f -> %.2f s (+%.2f)" % (f["text"], f["from"], f["to"], f["to"] - f["from"])
                                    for f in fixes)), file=sys.stderr)
            drift = max(abs(a["start"] - self.shifted(b["start"])) for a, b in zip(caps, v3caps))
            print("\nCAPTION TIMING vs v3: max start difference %.2f s (v3 used uncorrected transcriber times, "
                  "which showed some captions before the speech)." % drift, file=sys.stderr)
            # picture: same frames at matching moments (v3 time -> shifted time)
            final = rep["final"]["file"]
            for t in (4, 12, 20, 28, 35, 38.5):
                err = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(self.shifted(t)), "-i", final, "-ss", str(t), "-i",
                                      str(V3 / "heating-tray-preview-v3-captions.mp4"), "-frames:v", "1", "-lavfi",
                                      "[0:v]scale=360:640,format=gray[a];[1:v]scale=360:640,format=gray[b];[a][b]ssim",
                                      "-f", "null", "-"], capture_output=True, text=True).stderr
                self.assertGreater(float(re.search(r"All:([0-9.]+)", err).group(1)), 0.97, "frame at v3 %ss differs" % t)
        print("\nCompared with v3 except the clip-13 trim (clip 13 kept whole, +0.125 s after 23.875 s).", file=sys.stderr)
        self.assertEqual(before, {p: sha(p) for p in self.watched()}, "Heating Tray sources and v3 output must not change")


if __name__ == "__main__":
    unittest.main(verbosity=2)
