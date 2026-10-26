---
name: flow-video
description: Produce an AI video with Google Flow (Omni clips by default, Nano Banana keyframes, Gemini voice) under strict approval gates, then assemble only approved clips with the studio's own tools (ffmpeg, HyperFrames). Use whenever the user asks to make, storyboard, generate, redo or assemble a Flow / Omni / Veo / AI UGC video or ad. Storyboard, keyframes and voice are approved before any video; every paid clip is approved one at a time; no automatic retries, variants, batches or model fallbacks.
---

# Flow video, one approved clip at a time

Studio: `~/Claude Code/video-studio`. Each video lives in `projects/<name>/`,
with `storyboard.json` as the single source of truth. Start it from
`templates/storyboard.template.json`. Run shell commands with
`export PATH="/opt/homebrew/bin:$PATH"` first.

Where files go: every working file and output lives in `projects/<name>/` (clips also stay where
flow-mcp saved them). Deliverables meant for the user to watch or keep (an approved clip for review,
a finished render) are additionally COPIED (never moved) to `~/Downloads/<Project>-<what>/`, unless
the user said to keep that task inside the project folder. Never overwrite an earlier version; write
each revision to a new folder or file name.

## Hard rules (never broken, even if asked to "just go faster")

1. No video is generated until the whole storyboard (Gate A), every keyframe (Gate B) and the
   voice lines (Gate C) are approved and recorded as approved in `storyboard.json`.
2. One paid clip per approval. Show the clip's approval card (Gate D), wait for the user's explicit
   "approve <scene id>", then make exactly ONE `flow_generate` call with exactly ONE scene, sending
   the scene's `video` block as it is: `model`, `aspect_ratio`, `duration`, `resolution`,
   `max_credits`, `variants: 1`, `retries: 0`, plus both approved keyframes as `first_frame` /
   `last_frame`.
3. No automatic retries, alternate takes, `flow_retry`, `chain_previous`, `flow_continue` or
   `flow_edit` unless the user approves that specific call after you explain why it's needed.
4. After a clip is generated, stop. Build the review pack, show it, and wait for the user's
   decision (Gate E) before showing the next card.
5. Never use the Flow Studio panel (127.0.0.1:8787), never click in the Flow website, and never run
   flow-mcp code outside its MCP tools. The credit guard hook (`~/.claude/hooks/flow_guard.py`)
   blocks most of this; follow these rules anyway.
6. If a paid call gets no permission prompt, or the prompt's reason doesn't start with
   "Flow credit guard: PAID-TOOL APPROVAL", deny it and tell the user the guard isn't working.
7. If a call answers `still_working` or times out, never submit it again. Use `flow_wait`, then
   `flow_assets` to see what really happened.
8. Automated checks only flag things for the user to look at. Never say a clip "passed" on visual
   quality, natural motion, likeness, product accuracy, voice consistency or lip sync: only the
   user can judge those.
9. Never change the model, remove a frame, raise `max_credits` or retry by yourself. If Flow
   refuses or can't do what the card says, stop, show the exact message and wait.

## Video model (default: Omni)

Default: **Omni 1.1 Flash**, 9:16, 720p. Only models on the credit guard's allowlist
(`VIDEO_MODELS` in `~/.claude/hooks/flow_guard.py`; today only Omni 1.1 Flash) can be used. Another
model needs the user's explicit approval, a guard update and a matching price table first; until
then don't plan scenes with it. Never fall back to another model yourself.
Every video call sets `model`, `duration`, `resolution` and `aspect_ratio` explicitly. A missing
`model` would keep whatever Flow last used, possibly Veo.

Omni settings come from `storyboard.json` → `model_profiles["Omni 1.1 Flash"]`:
- duration: 4 | 6 | 8 | 10 s per scene, chosen from that scene's approved voice line:
  the smallest length that is at least the TTS line + 0.5 s. If a line needs more than 10 s,
  split the scene and get the storyboard approved again (Gate A).
- resolution: 720p by default; 360p only if the user picks it for a scene.
- credits: one price source. The guard's table is the one enforced; the storyboard's
  `model_profiles[...].credits` must match it exactly, and `prices_approved_by_user` stays `false`
  until the user approves the table (both checked by `scripts/storyboard_check.py`, which refuses
  every card otherwise). The table is an ESTIMATE from flow-mcp notes, not a live quote.
  `expected_credits` = the table value; `max_credits` = that same number. flow-mcp reads Flow's live
  price before spending and skips the scene (nothing spent) if it's above `max_credits`. Report
  that and make a new card. Never raise the cap or change a price yourself: a new price needs the
  user's approval, then the guard and the storyboard updated together.
- If Flow answers "Model … not found", "Flow has no … option", or anything that says the frames
  can't be used, stop and show the user the message (it lists what Flow offers). Don't switch models,
  drop a frame, change duration or resolution, or retry until the user approves a specific change.

Limits to tell the user at Gate A (from `model_profiles[...].verified`):
- Omni with an END frame is untested. The first approved clip checks it (review pack: end-frame
  similarity). If Omni ignores or misses end frames, joins can't be made exact; the user picks the
  fix (shorter shots, hard cuts, or a different model for that scene).
- It's untested whether Omni produces audio, speech or lip movement. The approved TTS line is always
  the final voice. If Omni makes no mouth movement for a spoken line, lip sync can't be fixed in the
  edit; decide after the first clip, don't assume.
- Frames and reference images can't be combined in one video generation (Flow's composer and model
  both refuse), so likeness during video comes only from the approved keyframes and the repeated
  character text. That reduces drift but doesn't guarantee a perfect match.

## Stage 1: Brief review (no tools that generate)
Read the user's brief, script, dialogue, reference images or videos and requirements. List what's
missing (product photos, target length, aspect ratio, voice preference, budget) and ask. Write it
into `brief` in a new `projects/<name>/storyboard.json` (status `draft`).

## Stage 2: Storyboard and continuity bible → GATE A
Fill `continuity` (character, clothing, product, environment, camera, lighting, negative prompt)
and `scenes` completely: beat, `shot_type` (talking = face speaking the line, a lip-sync risk;
cutaway = voice over product/hands/angles), exact dialogue, target length, action, start and end
keyframe, full prompt, and the `video` block. Run
`python3 scripts/storyboard_check.py projects/<name>/storyboard.json` (with the real folder name in
place of `<name>`; no angle brackets in the shell) after every storyboard change; fix every ERROR
before showing a gate. Each scene is one shot that runs from one keyframe to the next,
so scene N's end keyframe is scene N+1's start keyframe.
Durations are provisional until Gate C (they follow the recorded lines).
Estimate total credits (one take per scene, from the price table, marked "estimate") against
`budget.credit_cap_total`.
Show the storyboard as a table (scene, line, words/seconds, start→end keyframe, action, model,
duration, resolution, estimated credits) plus the Omni limits above. Mark which scene is the hook and
which is the ending/CTA, and give each scene's purpose in its `beat`; if the script has no clear hook
or ending/CTA, say so at Gate A instead of filling one in.
**Wait for "storyboard approved"**, then set `approvals.storyboard`.

## Stage 3: Keyframes (free stills, each call approved) → GATE B
- Make K0 (the master frame) first, with the product photos as `reference_images`. Images can use
  references; video frame mode can't. Keyframes use the video's aspect ratio (9:16).
- Make every other keyframe as an edit of the approved K0 (K0 as a reference, prompt starting
  "Recreate the reference image exactly… Only change: …"), so face, clothing, room and product
  carry over. Optionally create a Flow character from the approved K0 for later stills.
- Show ALL keyframes together (a contact sheet, plus each file path) beside the continuity
  bible. **Wait for approval of every keyframe.** Redraw only the rejected ones, each as its own
  approved call. Record each keyframe's `status` and `file`.

## Stage 4: Dialogue and voice → GATE C
- Lock `voice` (engine, voice, style prompt, per-line notes). Run `flow_voices` (read-only) to
  confirm the voice exists. Tell the user the narration limits before starting (believed to be
  about 3/min and 10/day for Gemini TTS; record what you confirm in `voice.limits_checked`).
- Record each line with `flow_narrate` (one approved call per line), using exactly the
  storyboard's wording. Measure each with ffprobe, transcribe it to confirm the words, and set the
  scene's Omni `duration_seconds`, `expected_credits` and `max_credits` from it.
- Put the line files, lengths and resulting clip durations/credits in front of the user. **Wait for
  approval of all lines.** The approved TTS line is the final voice.

## Stage 5: One paid clip → GATE D → generate → GATE E (repeat per scene, in order)
**D. Approval card:** generate it, don't write it by hand:
`python3 scripts/storyboard_check.py projects/<name>/storyboard.json --card S03`. Show its output
exactly, then stop. If it prints "CARD REFUSED", show the reasons and fix them; never improvise a
card. Its format:
```
CLIP S03 of 5 · "<beat>"
Start → end : K2 (path) → K3 (path)        [both approved]
Model       : Omni 1.1 Flash · 6 s · 720p · 9:16 · 1 take · no retries
Credits     : estimated 10 (price table, not live) · max_credits 10
              Flow's live price is checked before spending; if it's higher, nothing is spent.
              Spent so far 22 / budget 80
Dialogue    : "<exact line>"  (TTS line approved, 5.2 s → 6 s clip)
Prompt      : <the full prompt exactly as it will be sent>
Unverified  : <list from model_profiles[...].verified that this clip depends on>
Reply "approve S03" to submit this one clip.
```
Change nothing after approval. If anything needs to change, show a new card.

**Submit:** one `flow_generate` call, `project` = `flow_project_folder`, one scene. Approve the
guard prompt with "Allow once" only. Set the scene's `generation.status` to `submitted`.
Wait with `flow_wait` until it finishes. If Flow refuses (price above `max_credits`, model or
option missing, frames not accepted) or the generation fails, report the exact message and stop:
never try again by yourself. Record the charged credits in `budget.ledger`.

**E. Review:** copy the clip to `~/Downloads` (per "Where files go" above), run
`python3 scripts/review_clip.py --storyboard projects/<name>/storyboard.json --scene S03 --clip <file>`
and show the report (`projects/<name>/review/S03-try<N>/report.html`, its frame images, its flags
and its limitations). Ask the user to watch the actual clip with sound. **Wait for: approve /
revise / reject.** For "revise": explain the likely cause (prompt wording, keyframe, a motion the
model can't do, a model limit) and the proposed fix, then show a new card. Record each try in
`attempts`. Show the next scene's card only after this one is decided.
After the FIRST Omni clip, update `model_profiles[...].verified` with what the user and the review
pack observed (end frame honoured? audio/speech present? 9:16 size?).

## Stage 6: Assembly (approved clips only)
- Use only scenes whose `review.status` is `approved`, in storyboard order.
- Who edits: Claude assembles and edits directly with the studio's own tools (ffmpeg for cuts and
  audio, HyperFrames for captions and graphics). Use CapCut only for a specific task this workflow
  can't do, and say which task and why before handing it over. Tella stays the tool for screen/camera
  recordings (`make-video` skill); use it here only if the user asks.
- Assemble with `python3 scripts/assemble.py projects/<name>/storyboard.json --out projects/<name>/renders/v1`
  (real folder name, no angle brackets; a new `--out` folder for every revision). It enforces the
  rules below: approved clips only, strict refusal of trims that cut speech, approved hard cuts,
  caption starts at the measured speech onset, no overwrites. Never hand-build an edit to get around
  one of its refusals; show the user the refusal and ask.
- Joins: clip N ends on the same keyframe clip N+1 starts on. By default never trim the end of a
  clip that meets the next scene; at a join, only drop clip N+1's first frame if it repeats clip N's
  last frame. Trim dead air only at the very start of the first clip and the very end of the last one.
  Exceptions, each shown to the user with frames and approved before rendering: (a) ending a clip
  early to remove a defect (a morph, a glitch), which makes that join a hard cut, recorded as
  `join_previous: "cut"` on the next scene; (b) an intended hard cut, also `join_previous: "cut"`;
  (c) on the final shot only, holding a steady last frame and fading picture and sound out together.
  Never hide a defect with a crossfade or an invisible trim; tell the user what was cut and why.
- Voice: put the approved TTS lines over the clips with one shared voice chain and one continuous
  room-tone track, and check levels. Lip timing needs its own look per project (the heating-tray
  sync scripts are not general yet).
- Music and sound effects: follow the `make-video` skill (Epidemic Sound first, free libraries if
  it can't download, 3 options, wait for the user's pick).
- Final QC before any export or publish. Show the user `report.json` and the contact sheets from the
  render folder, split into:
  automated (from assemble.py): final loudness and peak, video vs audio duration, frame similarity at
  each join, room-tone steadiness, transcript vs script (word match), caption count and starts at the
  measured speech onset, output size set from `format.final_resolution` (checked in its tests, not
  re-measured per render: confirm with ffprobe);
  human review (say so explicitly, never mark as passed yourself): character and product continuity,
  product and packaging accuracy, voice identity, lip sync, room sound, pacing, transitions, words cut
  at joins, caption readability and placement, and the actual video watched with sound.
- Any failed check, refusal or open question (checker, guard, assemble.py, review pack, the user's
  review) pauses the workflow: show it and ask the user what to do. Never bypass a safeguard, retry a
  generation or spend credits to get past it.
