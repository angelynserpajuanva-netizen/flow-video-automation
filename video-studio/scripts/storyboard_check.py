#!/usr/bin/env python3
"""Check a flow-video storyboard.json before anything is spent, and print a paid-clip approval card.

Usage (copy-paste; the example project folder is "cozy-mug", put your project's folder name there):
  cd ~/"Claude Code"/video-studio
  mkdir -p projects/cozy-mug && cp -n templates/storyboard.template.json projects/cozy-mug/storyboard.json
  python3 scripts/storyboard_check.py projects/cozy-mug/storyboard.json             # validate
  python3 scripts/storyboard_check.py projects/cozy-mug/storyboard.json --card S01  # validate, then card for S01
  python3 -I scripts/tests/test_storyboard_check.py                                 # offline regression tests
  options: --guard PATH (default ~/.claude/hooks/flow_guard.py), --json
  Don't type angle brackets like <name> in zsh: it reads "<" as "take input from a file".

Exit codes: 0 valid, 1 errors found, 2 card refused. Fails closed: an unreadable guard, a price the
guard doesn't have, a mismatch with the storyboard's table, or a price table the user hasn't approved
is an error, never a silent choice. Read-only: it never edits files or calls Flow.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

GATES = ("storyboard", "keyframes", "voice")  # A, B, C in order
SHOT_TYPES = ("talking", "cutaway")
GEN_STATES = ("not_started", "card_shown", "approved_to_submit", "submitted", "downloaded", "failed")
REVIEW_STATES = ("pending", "approved", "revise", "rejected")
IN_FLIGHT = ("approved_to_submit", "submitted")
TTS_MARGIN = 0.5      # seconds of clip after the last word
MAX_WORDS_PER_S = 3.3  # faster than this rarely sounds natural


def load_guard_prices(path):
    """{model: {resolution: {duration: credits}}} from the guard's VIDEO_MODELS, parsed (never executed)."""
    tree = ast.parse(Path(path).read_text())
    literals, models = {}, None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "VIDEO_MODELS":
                models = node.value
            else:
                try:
                    literals[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    if not isinstance(models, ast.Dict):
        raise ValueError("no VIDEO_MODELS table found")
    out = {}
    for k, v in zip(models.keys, models.values):
        table = literals[v.id] if isinstance(v, ast.Name) else ast.literal_eval(v)
        out[ast.literal_eval(k)] = {r: {int(d): c for d, c in t.items()} for r, t in table.items()}
    return out


class Check:
    def __init__(self, sb, base, guard_path):
        self.sb, self.base, self.errors, self.warnings = sb, base, [], []
        self.guard_path = guard_path
        self.guard = None

    def err(self, where, msg):
        self.errors.append("%s: %s" % (where, msg))

    def warn(self, where, msg):
        self.warnings.append("%s: %s" % (where, msg))

    def path(self, p):
        q = Path(p).expanduser()
        return q if q.is_absolute() else self.base / q

    def need(self, obj, keys, where):
        for k in keys:
            v = obj.get(k) if isinstance(obj, dict) else None
            if v is None or v == "" or v == [] or v == {}:
                self.err(where, "missing required field '%s'" % k)

    # -- pricing --------------------------------------------------------------------------------------
    def prices(self):
        try:
            self.guard = load_guard_prices(self.guard_path)
        except Exception as e:  # fail closed
            self.err("pricing", "cannot read the guard's price table at %s (%s); nothing can be priced" % (self.guard_path, e))
            return
        profiles = self.sb.get("model_profiles") or {}
        used = {(s.get("video") or {}).get("model") for s in self.sb.get("scenes", [])} | {self.sb.get("default_video_model")}
        for model in sorted(m for m in used if m):
            if model not in self.guard:
                self.err("pricing", "model '%s' is not on the guard's allowlist (%s); adding it needs your approval and a guard update"
                         % (model, ", ".join(self.guard)))
                continue
            prof = profiles.get(model)
            if not prof or not prof.get("credits"):
                self.err("pricing", "storyboard has no price table for '%s'" % model)
                continue
            mine = {r: {int(d): c for d, c in t.items()} for r, t in prof["credits"].items()}
            if mine != self.guard[model]:
                self.err("pricing", "'%s' prices differ between storyboard %s and guard %s; approve one table and update the other"
                         % (model, json.dumps(mine, sort_keys=True), json.dumps(self.guard[model], sort_keys=True)))
            if prof.get("prices_approved_by_user") is not True:
                self.err("pricing", "'%s' prices are an unverified estimate (%s); set model_profiles['%s'].prices_approved_by_user "
                         "to true only after you approve them" % (model, prof.get("credits_source", "no source"), model))

    def price(self, video):
        try:
            return self.guard[video["model"]][video["resolution"]][video["duration_seconds"]]
        except (KeyError, TypeError):
            return None

    # -- structure ------------------------------------------------------------------------------------
    def run(self):
        sb = self.sb
        if sb.get("schema") != "flow-video/1":
            self.err("storyboard", "schema must be 'flow-video/1'")
        self.need(sb, ("project", "flow_project_folder", "format", "budget", "approvals", "default_video_model",
                       "model_profiles", "continuity", "keyframes", "scenes"), "storyboard")
        fmt = sb.get("format") or {}
        if fmt.get("aspect_ratio") not in ("9:16", "16:9"):
            self.err("format", "aspect_ratio must be '9:16' or '16:9'")
        cont = sb.get("continuity") or {}
        for block in ("character", "product", "environment"):
            if not (cont.get(block) or {}).get("lock_text"):
                self.err("continuity", "%s.lock_text is empty; every prompt repeats it word for word" % block)
        self.need(cont, ("camera", "lighting", "negative"), "continuity")
        self.prices()
        self.gates()
        self.keyframes()
        self.scenes()
        self.budget()
        return self

    def gate(self, g):
        return ((self.sb.get("approvals") or {}).get(g) or {}).get("status")

    def gates(self):
        for i, g in enumerate(GATES):
            st = self.gate(g)
            if st not in ("pending", "approved"):
                self.err("approvals", "%s status must be 'pending' or 'approved'" % g)
            if g == "voice" and st == "approved":
                # the voice identity is a reusable reference: every line must use the same documented voice
                self.need(self.sb.get("voice") or {}, ("engine", "voice", "style_prompt"), "voice")
            if st == "approved" and any(self.gate(e) != "approved" for e in GATES[:i]):
                self.err("approvals", "gate '%s' is approved but an earlier gate (%s) is not; gates go storyboard -> keyframes -> voice"
                         % (g, ", ".join(e for e in GATES[:i] if self.gate(e) != "approved")))

    def keyframes(self):
        self.kf = {}
        for k in self.sb.get("keyframes") or []:
            kid = k.get("id")
            if not kid or kid in self.kf:
                self.err("keyframes", "missing or duplicate id '%s'" % kid)
                continue
            self.kf[kid] = k
            if self.gate("keyframes") == "approved":
                if k.get("status") != "approved":
                    self.err("keyframe %s" % kid, "keyframes gate is approved but this keyframe is '%s'" % k.get("status"))
                if not k.get("file") or not self.path(k["file"]).exists():
                    self.err("keyframe %s" % kid, "approved keyframe has no file on disk (%s)" % k.get("file"))

    def scenes(self):
        scenes = self.sb.get("scenes") or []
        orders = [s.get("order") for s in scenes]
        if orders != list(range(1, len(scenes) + 1)):
            self.err("scenes", "order must run 1..%d in list order (got %s)" % (len(scenes), orders))
        ids = [s.get("id") for s in scenes]
        if len(set(ids)) != len(ids) or None in ids:
            self.err("scenes", "scene ids must be present and unique")
        all_approved = all(self.gate(g) == "approved" for g in GATES)
        in_flight = [s.get("id") for s in scenes if (s.get("generation") or {}).get("status") in IN_FLIGHT]
        if len(in_flight) > 1:
            self.err("scenes", "only one clip may be approved/submitted at a time; in flight: %s" % ", ".join(in_flight))
        prev = None
        for s in scenes:
            self.scene(s, prev, all_approved)
            prev = s

    def scene(self, s, prev, all_approved):
        w = "scene %s" % s.get("id")
        self.need(s, ("id", "beat", "start_keyframe", "end_keyframe", "prompt", "video"), w)
        shot = s.get("shot_type")
        if shot not in SHOT_TYPES:
            self.err(w, "shot_type must be 'talking' or 'cutaway' (plans lip-sync risk before generation)")
        if shot == "talking" and not s.get("dialogue"):
            self.err(w, "a talking shot needs its exact dialogue")
        for side in ("start_keyframe", "end_keyframe"):
            if s.get(side) and s[side] not in self.kf:
                self.err(w, "%s '%s' is not in keyframes" % (side, s[side]))
        if prev is not None and prev.get("end_keyframe") != s.get("start_keyframe"):
            if s.get("join_previous") == "cut":
                self.warn(w, "hard cut from %s (join_previous: cut): no matching frame at this join" % prev.get("id"))
            else:
                self.err(w, "keyframe chain broken: previous scene ends on %s but this starts on %s (set join_previous: 'cut' "
                            "only if a hard cut is intended)" % (prev.get("end_keyframe"), s.get("start_keyframe")))
        self.video(s, w)
        self.timing(s, w)
        gen = (s.get("generation") or {}).get("status", "not_started")
        rev = (s.get("review") or {}).get("status", "pending")
        if gen not in GEN_STATES:
            self.err(w, "generation.status '%s' is not one of %s" % (gen, ", ".join(GEN_STATES)))
        if rev not in REVIEW_STATES:
            self.err(w, "review.status '%s' is not one of %s" % (rev, ", ".join(REVIEW_STATES)))
        if gen != "not_started" and not all_approved:
            self.err(w, "generation has started (%s) before storyboard, keyframes and voice were all approved" % gen)
        if gen != "not_started" and prev is not None and (prev.get("review") or {}).get("status") not in ("approved", "rejected"):
            self.err(w, "generation started before the previous scene %s was reviewed" % prev.get("id"))
        if rev == "approved" and (gen != "downloaded" or not (s.get("generation") or {}).get("clip_file")):
            self.err(w, "review is approved but no downloaded clip is recorded")

    def video(self, s, w):
        v = s.get("video") or {}
        self.need(v, ("model", "aspect_ratio", "resolution"), w + " video")
        if v.get("aspect_ratio") and v["aspect_ratio"] != (self.sb.get("format") or {}).get("aspect_ratio"):
            self.err(w, "video.aspect_ratio %s differs from format.aspect_ratio" % v["aspect_ratio"])
        if v.get("variants", 1) != 1:
            self.err(w, "variants must be 1")
        if v.get("retries") != 0:
            self.err(w, "retries must be 0 (no automatic retries)")
        table = (self.guard or {}).get(v.get("model"), {})
        res = table.get(v.get("resolution"))
        if table and res is None:
            self.err(w, "resolution '%s' not offered for %s (%s)" % (v.get("resolution"), v.get("model"), ", ".join(table)))
        d = v.get("duration_seconds")
        if d is not None and res is not None and (isinstance(d, bool) or d not in res):
            self.err(w, "duration %s s not offered for %s %s (allowed: %s)" % (d, v.get("model"), v.get("resolution"), sorted(res)))
        p = self.price(v) if self.guard else None
        if p is not None:
            if v.get("expected_credits") != p:
                self.err(w, "expected_credits %s should be %s (guard price for %s s %s)" % (v.get("expected_credits"), p, d, v.get("resolution")))
            if v.get("max_credits") != p:
                self.err(w, "max_credits %s must equal the approved price %s (never raised automatically)" % (v.get("max_credits"), p))

    def timing(self, s, w):
        v = s.get("video") or {}
        d, tts = v.get("duration_seconds"), s.get("tts_seconds")
        res = ((self.guard or {}).get(v.get("model"), {})).get(v.get("resolution"), {})
        voiced = bool(s.get("dialogue"))
        if voiced and self.gate("voice") == "approved" and (s.get("tts_status") != "approved" or not s.get("tts_file") or tts is None):
            self.err(w, "voice gate is approved but this line has no approved TTS file and length")
        if voiced and s.get("tts_file") and s.get("tts_status") == "approved" and not self.path(s["tts_file"]).exists():
            self.err(w, "approved TTS file missing on disk (%s)" % s["tts_file"])
        if voiced and tts is not None and res:
            need = tts + TTS_MARGIN
            fits = [x for x in sorted(res) if x >= need - 1e-9]
            if not fits:
                self.err(w, "TTS line is %.2f s; with %.1f s margin it needs %.2f s, longer than the longest clip (%d s). Split the scene"
                         % (tts, TTS_MARGIN, need, max(res)))
            elif d != fits[0]:
                self.err(w, "duration should be %d s (smallest clip >= TTS %.2f s + %.1f s), not %s" % (fits[0], tts, TTS_MARGIN, d))
        elif voiced and d is None:
            self.warn(w, "duration not set yet; it comes from the recorded TTS line (Gate C)")
        if voiced and d:
            words = len(re.findall(r"[A-Za-z0-9']+", s["dialogue"]))
            rate = words / max(d - TTS_MARGIN, 0.1)
            if rate > MAX_WORDS_PER_S:
                self.warn(w, "%d words in %s s is %.1f words/s; likely rushed" % (words, d, rate))

    def budget(self):
        b = self.sb.get("budget") or {}
        cap = b.get("credit_cap_total")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            self.err("budget", "credit_cap_total must be a positive whole number")
            return
        ledger = sum(e.get("credits", 0) for e in b.get("ledger") or [])
        if b.get("credits_spent", 0) != ledger:
            self.err("budget", "credits_spent %s does not match the ledger total %s" % (b.get("credits_spent"), ledger))
        remaining = sum((s.get("video") or {}).get("expected_credits") or 0 for s in self.sb.get("scenes", [])
                        if (s.get("review") or {}).get("status") != "approved")
        unpriced = [s.get("id") for s in self.sb.get("scenes", []) if (s.get("video") or {}).get("expected_credits") is None]
        if unpriced:
            self.warn("budget", "not priced yet: %s (total is incomplete)" % ", ".join(unpriced))
        self.total = (b.get("credits_spent", 0), remaining, cap)
        if b.get("credits_spent", 0) + remaining > cap:
            self.err("budget", "spent %s + remaining one-take estimate %s = %s exceeds the cap of %s"
                     % (b.get("credits_spent", 0), remaining, b.get("credits_spent", 0) + remaining, cap))


def card(chk, scene_id):
    """Gate D approval card, or the reasons it can't be shown. Never shown when validation failed."""
    sb = chk.sb
    scenes = sb.get("scenes") or []
    s = next((x for x in scenes if x.get("id") == scene_id), None)
    why = []
    if chk.errors:
        why.append("the storyboard has %d validation error(s); fix them first" % len(chk.errors))
    if s is None:
        return None, why + ["no scene '%s'" % scene_id]
    why += ["gate '%s' not approved" % g for g in GATES if chk.gate(g) != "approved"]
    gen = (s.get("generation") or {}).get("status", "not_started")
    if gen not in ("not_started", "card_shown", "failed"):
        why.append("scene is already '%s'" % gen)
    for x in scenes:
        if x is s:
            break
        if (x.get("review") or {}).get("status") not in ("approved", "rejected"):
            why.append("earlier scene %s is not reviewed yet" % x.get("id"))
    busy = [x.get("id") for x in scenes if x is not s and (x.get("generation") or {}).get("status") in IN_FLIGHT]
    if busy:
        why.append("another clip is in flight: %s" % ", ".join(busy))
    for side in ("start_keyframe", "end_keyframe"):
        k = chk.kf.get(s.get(side) or "")
        if not k or k.get("status") != "approved" or not k.get("file"):
            why.append("%s is not an approved keyframe with a file" % side)
    if s.get("dialogue") and s.get("tts_status") != "approved":
        why.append("dialogue TTS not approved")
    if why:
        return None, why
    v = s["video"]
    b = sb["budget"]
    unverified = [k for k, val in ((sb["model_profiles"].get(v["model"]) or {}).get("verified") or {}).items()
                  if "UNVERIFIED" in str(val).upper()]
    args = {"project": sb["flow_project_folder"], "scenes": [{
        "prompt": s["prompt"], "type": "video", "model": v["model"], "aspect_ratio": v["aspect_ratio"],
        "duration": v["duration_seconds"], "resolution": v["resolution"], "max_credits": v["max_credits"],
        "variants": 1, "retries": 0,
        "first_frame": str(chk.path(chk.kf[s["start_keyframe"]]["file"])),
        "last_frame": str(chk.path(chk.kf[s["end_keyframe"]]["file"]))}]}
    n = scenes.index(s) + 1
    lines = [
        "CLIP %s of %d · \"%s\" · %s shot" % (s["id"], len(scenes), s["beat"], s["shot_type"]),
        "Start → end : %s (%s) → %s (%s)   [both approved]" % (s["start_keyframe"], args["scenes"][0]["first_frame"],
                                                             s["end_keyframe"], args["scenes"][0]["last_frame"]),
        "Model       : %s · %s s · %s · %s · 1 take · no retries" % (v["model"], v["duration_seconds"], v["resolution"], v["aspect_ratio"]),
        "Credits     : estimated %s (approved price table, not a live quote) · max_credits %s" % (v["expected_credits"], v["max_credits"]),
        "              Flow's live price is checked before spending; if it is higher, nothing is spent.",
        "              Spent so far %s / budget %s" % (b.get("credits_spent", 0), b["credit_cap_total"]),
        "Dialogue    : \"%s\"%s" % (s.get("dialogue") or "", "  (TTS approved, %.2f s → %s s clip)" % (s["tts_seconds"], v["duration_seconds"]) if s.get("dialogue") else ""),
        "Prompt      : %s" % s["prompt"],
        "Unverified  : %s" % (", ".join(unverified) or "none listed"),
        "Exact call  : flow_generate %s" % json.dumps(args, ensure_ascii=False),
        "Reply \"approve %s\" to submit this one clip." % s["id"],
    ]
    return "\n".join(lines), []


def main():
    ap = argparse.ArgumentParser(
        description="Validate a flow-video storyboard.json (read-only).",
        epilog="example (from ~/\"Claude Code\"/video-studio): python3 scripts/storyboard_check.py "
               "projects/cozy-mug/storyboard.json --card S01   |   exit codes: 0 valid, 1 errors, 2 card refused")
    ap.add_argument("storyboard")
    ap.add_argument("--card", metavar="SCENE_ID")
    ap.add_argument("--guard", default=str(Path("~/.claude/hooks/flow_guard.py").expanduser()))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    p = Path(a.storyboard).expanduser().resolve()
    try:
        sb = json.loads(p.read_text())
    except Exception as e:
        print("ERROR storyboard: cannot read %s (%s)" % (p, e))
        if not p.exists():
            print("      To start a project here: mkdir -p \"%s\" && cp -n templates/storyboard.template.json \"%s\""
                  % (p.parent, p))
        sys.exit(1)
    chk = Check(sb, p.parent, a.guard).run()
    text, refused = card(chk, a.card) if a.card else (None, [])
    if a.json:
        print(json.dumps({"errors": chk.errors, "warnings": chk.warnings, "card": text, "card_refused": refused}, indent=2))
    else:
        for e in chk.errors:
            print("ERROR " + e)
        for w in chk.warnings:
            print("WARN  " + w)
        spent, remaining, cap = getattr(chk, "total", (None, None, None))
        print("%s: %d error(s), %d warning(s)%s" % (p.name, len(chk.errors), len(chk.warnings),
              "; credits spent %s + remaining estimate %s of cap %s" % (spent, remaining, cap) if cap else ""))
        if a.card:
            print()
            print(text if text else "CARD REFUSED for %s:\n  - %s" % (a.card, "\n  - ".join(refused)))
    sys.exit(2 if a.card and not text else (1 if chk.errors else 0))


if __name__ == "__main__":
    main()
