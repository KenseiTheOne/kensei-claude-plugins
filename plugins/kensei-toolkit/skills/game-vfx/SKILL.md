---
name: game-vfx
description: Make or fix a game visual effect (VFX) in Unity — fire, explosion, lightning, water, wind, magic, hit, projectile, aura, ground zone — built from Particle System layers and shaders, with textures that a script computes or Codex draws. For a new effect it writes the brief first (one-line personality, three-phase timing in seconds, layers, hex palette, bans with reasons) and blocks the timing in with grey primitives; for a complaint about an existing one it films it, names the layer at fault and changes only that. Prompts Codex from templates, cleans every image with a script, films a fixed-rate clip, measures its timing and makes a numbered contact sheet, and shows each version to the user before the next change. Reads the project's own VFX rules first. Use when the user asks for a visual effect, a texture for one, or a change to one — "make a fire VFX", "the explosion feels weak", "particles for the hit", "a texture for the spark particles", «сделай эффект», «эффект взрыва», «VFX», «партиклы», «текстура для партиклов», «вспышка удара», «эффект снаряда», «эффект какой-то вялый», «фаербол вялый», «взрыв висит», «молния выгорает в белое», «переделай огонь». Not for UI animation, a post-processing setup alone, or a picture that is not part of a game effect (that is codex-img).
argument-hint: "[effect name or what to change]"
---

# game-vfx — game effects from a brief, one version at a time

A good effect is three to six simple layers with different timing, whose silhouette reads even in
greyscale — not one beautiful texture. So the work goes in this order: what the effect says and when
(brief, timing), then shape, then textures and colour. Codex draws only what a script cannot compute;
particles and shaders give shape over time, rhythm and colour.

Talk to the user in the user's language: questions, progress lines, the brief and the report. Files
you write for the project follow the project's language.

## Non-negotiables

1. **One version, then the user.** Build a version, show it, and change the next thing only from the
   user's words. No automated art-director rounds, no review/fix loops in Workflow or subagents, no
   self-scored iterations, no re-generating an image because you dislike its look. Why: such loops once
   burnt billions of tokens while their scores never converged — the user decides what reads right.
   Before showing you may fix, once, only a *measurable* miss: a red project test, a mechanical
   texture failure (`check` problems), an impact whose `timing` note says the peak is late, a life
   that differs from the brief. Never taste.
2. **Timing before textures.** For a new effect, no Codex image is generated before the rhythm is
   settled on grey primitives and the user has seen it (Step 2). Re-timing a grey ball is cheap; a
   texture drawn for the wrong rhythm is quota spent twice.
3. **A complaint names its layer.** Before changing anything after feedback, decide which layer is at
   fault (`brief.md`, feedback dictionary). Never regenerate a texture when a curve is the problem.
4. **Project rules beat this skill.** Read them first (Step 0). An effect the project marks approved
   or frozen is changed only when the user names that effect; then compare its reference frames before
   and after and update its guards in the same change. Shared shaders, materials and emitters an
   approved effect uses get the same before/after comparison.
5. **Numbers are labelled.** In briefs and reports, say which timing numbers are sourced and which are
   your heuristics (`timing.md`). Never invent a ratio and present it as a standard.

## Files of this skill

- `timing.md` — the three phases, sourced numbers vs heuristics, phases → Particle System, what the
  clip check measures. Read before Step 1 (or before the change route).
- `brief.md` — brief template, shape and colour rules, bad/good example, feedback template and the
  dictionary from user words to layers and knobs. Read before Step 1 (or before the change route).
- `unity.md` — layers to modules, prefab structure, blend modes and traps, shaders, budget, tilted
  camera, checking on a phone. Read before Step 2 (the block-in already builds particle systems).
- `textures.md` — compute or draw, prompt rules and templates, cleaning, import. Read before Step 3.
- `project-rules-template.md` — the shape of a project's VFX rules file.
- `vfx_tex.py` — textures: `prep`, `check`, `tile`, `noise`, `glow`, `ring`, `ramp`.
- `vfx_clip.py` — clips: `timing` (events, onset, peak, life, peak share, bright time, near-white) and
  `sheet` (numbered contact sheet over the effect, optional reference below).

## Setup — the tools' Python

The scripts need numpy, Pillow and imageio-ffmpeg (which brings its own ffmpeg), in one cached
environment. Create it once if the interpreter is missing:

- macOS / Linux: `python3 -m venv ~/.cache/game-vfx/.venv && ~/.cache/game-vfx/.venv/bin/pip install -q numpy pillow imageio-ffmpeg`
  — interpreter `~/.cache/game-vfx/.venv/bin/python`.
- Windows: `py -3 -m venv %USERPROFILE%\.cache\game-vfx\.venv` and the same `pip install` with
  `%USERPROFILE%\.cache\game-vfx\.venv\Scripts\python.exe -m pip`; that `python.exe` is the interpreter.

Run every script as `<interpreter> -I "${CLAUDE_SKILL_DIR}/vfx_tex.py" …` (`-I` keeps a stray module in
the working folder from being imported). Each prints one JSON object; `"ok": false` carries the error.

## Step 0 — Read the project's VFX rules

Look, in order, for `docs/vfx-rules.md`, `.claude/vfx-rules.md`, then a VFX section that the
project's `CLAUDE.md` or `docs/` point to. Read what you find in full: renderer limits, the shared
shader and its knobs, forbidden modules, palette ceilings, ownership of motifs, approved and frozen
effects, how to build atlases, how to film, which tests guard VFX, how a block-in is made there.

No rules file and the project has more than one effect ahead: offer once to start one from
`project-rules-template.md`, filled from what the code shows. Write it only on a yes. Without one,
assume a generic URP 3D project and say so in the brief.

Rules that contradict this skill (no HDR so no Bloom; gravity forbidden; colour atlas instead of grey
masks; a different capture rate) — follow the project and drop the skill's advice silently.

Then choose the route: **a new effect** → Steps 1–7. **A change to an existing effect** («вялый»,
«висит», «сделай ярче», a new texture for it) → the change route below.

## Change route — an existing effect

1. Film the effect as it is now (Step 5 recipe) and run `timing` and `sheet`. Find which part the
   user means (projectile in flight, the hit, the trail, the ground mark) — ask only if the clip does
   not make it obvious.
2. Translate the user's words with the dictionary in `brief.md` into one layer, one knob and a
   direction, and say it back in one line («вялый» → "резче пик удара и пауза перед угольками —
   правильно понял?"). Wait for the answer when the words were vague.
3. Change only that layer on the real effect: no grey rebuild, no new Codex image unless the texture
   layer is the one at fault. A brief is written only if the effect has none — then just the
   personality line and the storyboard read off the clip.
4. Step 5 (measure, project tests), then Step 6 (show and wait).

## Step 1 — The brief (new effect)

Fill the template from `brief.md` yourself from what is known: the request, the project rules, the
effect's neighbours (same element, same frequency), the gameplay numbers in the project's balance.
Mandatory fields: personality line, meaning and frequency, size in game units, the three-phase
storyboard in seconds, layers, palette in hex with blend mode (with a hue shift from core to edge),
how it differs from its nearest neighbour on at least two axes, bans with reasons, acceptance.

Show the brief to the user with the gaps marked. Ask only the questions whose answer changes the
result — at most three, in one `AskUserQuestion` (header "VFX brief"), each with a recommended
option: typically frequency (dozens per second or once in a while — this sets size and loudness), the
reference to follow and what to take from it, what it must not be confused with.

Save the agreed brief next to the effect's working files (the project's convention, else
`.tmp/vfx/<effect>/brief.md`). It is the acceptance list for every later step.

## Step 2 — Block in the timing (new effect)

Build the effect from neutral shapes only, no Codex textures, no colour, no dissolve: the project's
block-in recipe when its rules give one (a project may require its own atlas and material on every
system — a generic default particle would fail its tests), else a `vfx_tex.py glow` disc and a mesh
ring. Set lifetimes, bursts, start delays and size, speed and alpha curves from the storyboard
(`timing.md`, mapping table; prefab structure and traps in `unity.md`).

Film it (Step 5), run `timing` and `sheet`, check the numbers against the brief, then **open the sheet
for the user** with the numbers and ask whether the rhythm is right. Textures wait for this answer.

## Step 3 — Textures

List every texture the effect needs: name, role (shape, mask, noise, ramp, trail), blend mode, size,
and **computed or drawn** (`textures.md`, first table). Then:

- **Computed** — `vfx_tex.py noise | glow | ring | ramp`. Exact, seamless, power-of-two, instant.
- **Drawn** — write the prompt from the templates in `textures.md` and generate through the
  `codex-img` skill (three or more images: list and cost first, wait for a yes). For a series, get one
  anchor approved by the user first and pass it as Image 1 to every later prompt.
- **Retries — only mechanical.** codex-img allows automatic retries; here a retry is allowed only for
  a mechanical failure (`no_alpha`, `check` problems: touches the border, background not black, no
  transparency). Whether an image *looks* right is the user's call: show it and ask. Put possible
  retries into the cost you announce.
- **Every drawn image** goes through `vfx_tex.py prep` (mode by blend: `glow`, `mask`, `solid`; trails
  `--strip`) and `vfx_tex.py check`, unless the project has its own atlas builder — then feed the raw
  sheet to it (as its rules say: background, mode per sprite) and use its checks. Look at the result
  yourself. Save the prompt and the reference list next to the file (`name.prompt.txt`).

## Step 4 — Assemble, layer by layer

Add layers in the order from `timing.md`, writing every layer's settings in the brief's language turned
into modules (`unity.md`): bursts, lifetimes as ranges, curves by shape, the dissolve curve on the
shader knob, blend per layer, budget. "Check each layer" means: the project's tests stay green and one
look at a sheet for breakage (wrong place, wrong blend, missing) — not a round of taste fixes.

Run the project's VFX tests (all of them, by name filter, not a hand-picked list). Red is fixed before
anything is shown.

## Step 5 — Film and measure

- Capture at a constant rate with fixed seeds — the project's capture tool and rate, else Unity
  Recorder with Playback = Constant at 30 or 60 fps. One cast per clip; start before the effect spawns
  so the first frame is a clean background. Film close-up and game scale (the player's camera and
  resolution), plus a dense-fight shot when the effect repeats.
- `vfx_clip.py timing <clip>` first. At game scale the effect is small in the frame: pass
  `--roi x,y,w,h` around it. If the effect is already on screen in frame 0, use `--baseline last`.
- Then `vfx_clip.py sheet <clip> <out.png>` — by default it spans the effect, not the empty lead-in;
  add `--ref <approved clip>` to put the reference below.
- Compare with the brief in seconds: life, rise, peak share (an impact peaks within the first quarter
  of life; a rise within ~0.1 s counts as instant), `bright_s` (how long the brightest pixels last —
  report it, do not "fix" it: a bright fire body counts too), near-white against the project ceiling
  measured by the project's own tool.

## Step 6 — Show and take feedback

Open the clip or sheet for the user (they must see it, not just read about it) and report in a few
lines: what changed since the last version and in which layer, the measured timing against the brief,
and one question — «читается ли как <personality>?». Then stop and wait.

Turn the answer into a change with the feedback dictionary (`brief.md`): which layer, which knob,
which direction. Say it back in one line before doing it when the complaint is vague. Change only that
layer, then Step 5 again.

## Step 7 — Approved

When the user approves: keep the reference clip and its sheet next to the effect, add the effect to
the project rules' approved list with the date and the user's words, and — when the project guards
frozen files by hash or by reference frames — update those guards in the same change. Commit only on
the user's explicit command.

## Report

After each version, in the user's language: the paths of the clip and sheet, the measured numbers
against the brief, what changed and which layer, what was not touched, the one question. Do not paste
diffs or describe pixels.
