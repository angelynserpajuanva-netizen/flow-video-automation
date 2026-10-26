"""Offline regression tests for scripts/storyboard_check.py. No Flow calls, no network, temp files only.

Run: cd ~/"Claude Code"/video-studio && python3 -I scripts/tests/test_storyboard_check.py
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parent.parent / "storyboard_check.py"
REAL_GUARD = Path("~/.claude/hooks/flow_guard.py").expanduser()
GUARD_SRC = '''OMNI_CREDITS = {"360p": {4: 4, 6: 5, 8: 6, 10: 7}, "720p": {4: 7, 6: 10, 8: 12, 10: 15}}
VIDEO_MODELS = {"Omni 1.1 Flash": OMNI_CREDITS}
'''
CARD_MARK = 'Reply "approve'


def valid_storyboard():
    omni = {"durations_seconds": [4, 6, 8, 10], "resolutions": ["360p", "720p"],
            "credits": {"360p": {"4": 4, "6": 5, "8": 6, "10": 7}, "720p": {"4": 7, "6": 10, "8": 12, "10": 15}},
            "credits_source": "ESTIMATE from notes", "prices_approved_by_user": True,
            "verified": {"end_frame": "UNVERIFIED", "start_frame": "paid test"}}
    approved = {"status": "approved", "approved_at": "2026-10-19", "note": ""}
    scene = lambda sid, order, start, end, shot, dialogue, tts, dur, credits: {
        "id": sid, "order": order, "beat": "beat " + sid, "shot_type": shot, "dialogue": dialogue,
        "tts_file": "tts_%s.wav" % sid if dialogue else None, "tts_seconds": tts,
        "tts_status": "approved" if dialogue else "pending",
        "start_keyframe": start, "end_keyframe": end, "action": "a", "prompt": "prompt " + sid,
        "video": {"model": "Omni 1.1 Flash", "aspect_ratio": "9:16", "duration_seconds": dur, "resolution": "720p",
                  "expected_credits": credits, "max_credits": credits, "variants": 1, "retries": 0},
        "generation": {"status": "not_started"}, "review": {"status": "pending"}, "attempts": []}
    return {
        "schema": "flow-video/1", "project": "test", "flow_project_folder": "Test",
        "format": {"aspect_ratio": "9:16", "final_resolution": "1080x1920"},
        "budget": {"credit_cap_total": 30, "credits_spent": 0, "ledger": []},
        "approvals": {"storyboard": dict(approved), "keyframes": dict(approved), "voice": dict(approved)},
        "default_video_model": "Omni 1.1 Flash", "model_profiles": {"Omni 1.1 Flash": omni},
        "voice": {"engine": "gemini", "voice": "Aoede", "style_prompt": "warm, relaxed"},
        "continuity": {"character": {"lock_text": "c"}, "product": {"lock_text": "p"}, "environment": {"lock_text": "e"},
                       "camera": "cam", "lighting": "light", "negative": "neg"},
        "keyframes": [{"id": k, "file": "%s.png" % k, "status": "approved"} for k in ("K0", "K1", "K2")],
        "scenes": [scene("S01", 1, "K0", "K1", "talking", "Hello there, this is a short test line.", 5.2, 6, 10),
                   scene("S02", 2, "K1", "K2", "cutaway", "", None, 4, 7)],
    }


class Fixture(unittest.TestCase):
    """Temp folder with placeholder keyframe/voice files and a guard copy; no tests of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name in ("K0.png", "K1.png", "K2.png", "tts_S01.wav"):
            (self.dir / name).write_bytes(b"x")
        self.guard = self.dir / "guard.py"
        self.guard.write_text(GUARD_SRC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, sb, card=None, guard=None):
        path = self.dir / "storyboard.json"
        path.write_text(json.dumps(sb))
        cmd = [sys.executable, "-I", str(CHECKER), str(path), "--guard", str(guard or self.guard)]
        if card:
            cmd += ["--card", card]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def expect_error(self, sb, needle, card="S01"):
        code, out = self.run_check(sb, card=card)
        self.assertNotEqual(code, 0, out)
        self.assertIn(needle, out)
        self.assertNotIn(CARD_MARK, out, "a card must never print when validation fails")


class StoryboardCheck(Fixture):
    # -- valid ---------------------------------------------------------------------------------------
    def test_valid_storyboard_and_card(self):
        code, out = self.run_check(valid_storyboard(), card="S01")
        self.assertEqual(code, 0, out)
        self.assertIn("0 error(s)", out)
        self.assertIn(CARD_MARK + ' S01"', out)
        self.assertIn('"retries": 0', out)
        self.assertIn('"max_credits": 10', out)
        self.assertIn("Unverified  : end_frame", out)

    def test_hard_cut_needs_explicit_flag_and_only_warns(self):
        sb = valid_storyboard()
        sb["scenes"][1]["start_keyframe"] = "K0"
        sb["scenes"][1]["join_previous"] = "cut"
        code, out = self.run_check(sb)
        self.assertEqual(code, 0, out)
        self.assertIn("WARN  scene S02: hard cut", out)

    # -- required fields -----------------------------------------------------------------------------
    def test_missing_scene_prompt(self):
        sb = valid_storyboard(); del sb["scenes"][0]["prompt"]
        self.expect_error(sb, "missing required field 'prompt'")

    def test_missing_continuity_lock(self):
        sb = valid_storyboard(); sb["continuity"]["product"]["lock_text"] = ""
        self.expect_error(sb, "product.lock_text is empty")

    def test_missing_shot_type(self):
        sb = valid_storyboard(); del sb["scenes"][0]["shot_type"]
        self.expect_error(sb, "shot_type must be 'talking' or 'cutaway'")

    def test_talking_shot_without_dialogue(self):
        sb = valid_storyboard(); sb["scenes"][1]["shot_type"] = "talking"
        self.expect_error(sb, "a talking shot needs its exact dialogue")

    # -- durations and TTS timing --------------------------------------------------------------------
    def test_duration_not_offered(self):
        sb = valid_storyboard(); sb["scenes"][1]["video"]["duration_seconds"] = 7
        self.expect_error(sb, "duration 7 s not offered")

    def test_duration_does_not_follow_tts(self):
        sb = valid_storyboard(); v = sb["scenes"][0]["video"]
        v["duration_seconds"], v["expected_credits"], v["max_credits"] = 8, 12, 12
        self.expect_error(sb, "duration should be 6 s")

    def test_tts_too_long_for_any_clip(self):
        sb = valid_storyboard(); sb["scenes"][0]["tts_seconds"] = 9.8
        self.expect_error(sb, "Split the scene")

    def test_voice_gate_needs_documented_voice(self):
        sb = valid_storyboard(); sb["voice"]["voice"] = ""
        self.expect_error(sb, "voice: missing required field 'voice'")

    def test_voice_gate_without_tts(self):
        sb = valid_storyboard(); sb["scenes"][0]["tts_status"] = "recorded"
        self.expect_error(sb, "no approved TTS file and length")

    def test_resolution_not_offered(self):
        sb = valid_storyboard(); sb["scenes"][1]["video"]["resolution"] = "1080p"
        self.expect_error(sb, "resolution '1080p' not offered")

    def test_retries_and_variants(self):
        sb = valid_storyboard(); sb["scenes"][0]["video"]["retries"] = 1; sb["scenes"][0]["video"]["variants"] = 2
        code, out = self.run_check(sb, card="S01")
        self.assertIn("retries must be 0", out); self.assertIn("variants must be 1", out)
        self.assertNotIn(CARD_MARK, out)

    # -- keyframe chain ------------------------------------------------------------------------------
    def test_broken_chain(self):
        sb = valid_storyboard(); sb["scenes"][1]["start_keyframe"] = "K0"
        self.expect_error(sb, "keyframe chain broken")

    def test_unknown_keyframe(self):
        sb = valid_storyboard(); sb["scenes"][1]["end_keyframe"] = "K9"
        self.expect_error(sb, "end_keyframe 'K9' is not in keyframes")

    def test_approved_keyframe_file_missing(self):
        sb = valid_storyboard(); sb["keyframes"][2]["file"] = "missing.png"
        self.expect_error(sb, "approved keyframe has no file on disk")

    # -- budget and pricing --------------------------------------------------------------------------
    def test_budget_overrun(self):
        sb = valid_storyboard(); sb["budget"]["credit_cap_total"] = 12
        self.expect_error(sb, "exceeds the cap of 12")

    def test_ledger_mismatch(self):
        sb = valid_storyboard(); sb["budget"]["credits_spent"] = 5
        self.expect_error(sb, "does not match the ledger total")

    def test_max_credits_above_price(self):
        sb = valid_storyboard(); sb["scenes"][0]["video"]["max_credits"] = 15
        self.expect_error(sb, "max_credits 15 must equal the approved price 10")

    def test_pricing_mismatch_storyboard_vs_guard(self):
        sb = valid_storyboard(); sb["model_profiles"]["Omni 1.1 Flash"]["credits"]["720p"]["6"] = 9
        self.expect_error(sb, "prices differ between storyboard")

    def test_prices_not_approved(self):
        sb = valid_storyboard(); del sb["model_profiles"]["Omni 1.1 Flash"]["prices_approved_by_user"]
        self.expect_error(sb, "unverified estimate")

    def test_model_not_on_guard_allowlist(self):
        sb = valid_storyboard(); sb["scenes"][0]["video"]["model"] = "Veo 3.1 - Fast"
        self.expect_error(sb, "not on the guard's allowlist")

    def test_guard_missing_fails_closed(self):
        code, out = self.run_check(valid_storyboard(), card="S01", guard=self.dir / "nope.py")
        self.assertNotEqual(code, 0); self.assertIn("cannot read the guard's price table", out)
        self.assertNotIn(CARD_MARK, out)

    def test_guard_without_table_fails_closed(self):
        bad = self.dir / "bad_guard.py"; bad.write_text("X = 1\n")
        code, out = self.run_check(valid_storyboard(), card="S01", guard=bad)
        self.assertIn("no VIDEO_MODELS table found", out); self.assertNotIn(CARD_MARK, out)

    # -- gates and one-at-a-time ---------------------------------------------------------------------
    def test_skipped_gate(self):
        sb = valid_storyboard(); sb["approvals"]["keyframes"]["status"] = "pending"
        self.expect_error(sb, "gate 'voice' is approved but an earlier gate (keyframes) is not")

    def test_generation_before_approvals(self):
        sb = valid_storyboard(); sb["approvals"]["voice"]["status"] = "pending"
        sb["scenes"][0]["generation"]["status"] = "submitted"
        self.expect_error(sb, "generation has started (submitted) before")

    def test_two_clips_in_flight(self):
        sb = valid_storyboard()
        sb["scenes"][0]["generation"]["status"] = "submitted"
        sb["scenes"][1]["generation"]["status"] = "approved_to_submit"
        self.expect_error(sb, "only one clip may be approved/submitted at a time")

    def test_card_refused_until_previous_reviewed(self):
        code, out = self.run_check(valid_storyboard(), card="S02")
        self.assertEqual(code, 2, out)
        self.assertIn("earlier scene S01 is not reviewed yet", out)
        self.assertNotIn(CARD_MARK, out)

    def test_card_refused_when_gate_pending(self):
        sb = valid_storyboard(); sb["approvals"]["voice"]["status"] = "pending"; sb["scenes"][0]["tts_status"] = "recorded"
        code, out = self.run_check(sb, card="S01")
        self.assertEqual(code, 2, out); self.assertIn("gate 'voice' not approved", out); self.assertNotIn(CARD_MARK, out)

    def test_approved_review_needs_clip(self):
        sb = valid_storyboard(); sb["scenes"][0]["review"]["status"] = "approved"
        self.expect_error(sb, "review is approved but no downloaded clip")

    # -- the real installed guard (read-only parse) --------------------------------------------------
    @unittest.skipUnless(REAL_GUARD.exists(), "installed guard not present")
    def test_installed_guard_prices_match_fixture(self):
        code, out = self.run_check(valid_storyboard(), guard=REAL_GUARD)
        self.assertEqual(code, 0, out)


TEMPLATE = CHECKER.parent.parent / "templates" / "storyboard.template.json"


class TemplateCompatibility(Fixture):
    """The shipped template must hold every field the checker needs, approve nothing, and fail closed."""

    def template(self):
        return json.loads(TEMPLATE.read_text())

    def test_template_has_safe_unapproved_defaults(self):
        t = self.template()
        self.assertTrue(all(g["status"] == "pending" for g in t["approvals"].values()))
        prof = t["model_profiles"][t["default_video_model"]]
        self.assertIs(prof["prices_approved_by_user"], False)
        self.assertTrue(all(k["status"] == "pending" and k["file"] is None for k in t["keyframes"]))
        for s in t["scenes"]:
            self.assertEqual(s["tts_status"], "pending")
            self.assertEqual(s["generation"]["status"], "not_started")
            self.assertEqual(s["review"]["status"], "pending")
            self.assertIn("shot_type", s)
            self.assertIn("join_previous", s)
            self.assertEqual((s["video"]["variants"], s["video"]["retries"]), (1, 0))
            self.assertEqual(s["video"]["model"], t["default_video_model"])
        self.assertEqual(t["budget"]["credit_cap_total"], 0)

    def test_blank_template_fails_closed_without_structural_errors(self):
        code, out = self.run_check(self.template())
        self.assertEqual(code, 1, out)
        self.assertIn("unverified estimate", out)
        for structural in ("is not in keyframes", "prices differ", "is not one of", "keyframe chain broken", "schema must"):
            self.assertNotIn(structural, out)
        code, out = self.run_check(self.template(), card="S01")
        self.assertEqual(code, 2, out)
        self.assertNotIn(CARD_MARK, out)

    def test_filled_template_becomes_valid(self):
        t = self.template()
        t.update(project="test", flow_project_folder="Test")
        for block in ("character", "product", "environment"):
            t["continuity"][block]["lock_text"] = block
        t["continuity"].update(camera="cam", lighting="light", negative="neg")
        t["budget"]["credit_cap_total"] = 20
        t["voice"].update(voice="Aoede", style_prompt="warm, relaxed")
        t["model_profiles"]["Omni 1.1 Flash"]["prices_approved_by_user"] = True
        for k in t["keyframes"]:
            k.update(file="%s.png" % k["id"], status="approved")
        s = t["scenes"][0]
        s.update(beat="hook", prompt="prompt", shot_type="talking", dialogue="Hello there, this is a test.",
                 tts_file="tts_S01.wav", tts_seconds=5.2, tts_status="approved")
        s["video"].update(duration_seconds=6, expected_credits=10, max_credits=10)
        for g in t["approvals"].values():
            g["status"] = "approved"
        code, out = self.run_check(t, card="S01")
        self.assertEqual(code, 0, out)
        self.assertIn(CARD_MARK + ' S01"', out)

    def test_usage_docs_use_real_commands(self):
        doc = CHECKER.read_text().split('"""')[1]
        self.assertNotIn("<name>", doc.replace("like <name>", ""))
        self.assertIn("python3 scripts/storyboard_check.py projects/cozy-mug/storyboard.json --card S01", doc)
        self.assertIn("python3 -I scripts/tests/test_storyboard_check.py", doc)

    def test_missing_storyboard_explains_how_to_start(self):
        r = subprocess.run([sys.executable, "-I", str(CHECKER), str(self.dir / "nope" / "storyboard.json")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("To start a project here", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
