---
name: game-vfx
description: Make or fix a game visual effect (VFX) in Unity — fire, explosion, lightning, water, wind, magic, hit, projectile, aura, ground zone — built from Particle System layers and shaders, with textures as shader data a script computes or Codex draws as single elements — Codex draws only the concept the user approves, never the effect itself. For a new effect it writes the brief first (personality, three-phase timing in seconds, layers, hex palette, bans) and blocks the timing in with grey primitives; for a complaint about an existing one it films it, names the layer at fault and changes only that. For a frame or clip to match it lists every layer and compares ours beside it. Prompts Codex from templates, cleans every image with a script, films a fixed-rate clip, measures its timing and makes a numbered contact sheet, and shows each version to the user before the next change. Reads the project's own VFX rules first. Use when the user asks for a visual effect, a texture for one, or a change to one — "make a fire VFX", "the explosion feels weak", "particles for the hit", "a texture for the spark particles", «сделай эффект», «эффект взрыва», «VFX», «партиклы», «текстура для партиклов», «вспышка удара», «эффект снаряда», «эффект какой-то вялый», «фаербол вялый», «взрыв висит», «молния выгорает в белое», «переделай огонь», «сделай как на видео», «как на референсе». Not for UI animation, a post-processing setup alone, or a picture that is not part of a game effect (that is codex-img).
argument-hint: "[effect name or what to change]"
---

# game-vfx — game effects from a brief, one version at a time

A good effect is several simple layers with different timing, whose silhouette reads even in
greyscale — not one beautiful texture. Three to six layers is the floor for a small, frequent hit, not
a ceiling: an effect matched to a reference gets every layer the reference has, each with its own
character, and cutting it down to "a ring, a flash and sparks" is the commonest rejection («ты всё
упрощаешь»). So the work goes in this order: what the effect says and when
(brief, timing), then shape, then textures and colour. Codex draws the concept — a picture of how the
effect should look, for the user to agree on. The effect itself is textures as data for a shader
(masks, noise, ramps, growth order, crack maps) plus the particles and shaders that build its form over
time, its rhythm and colour.

Talk to the user in the user's language: questions, progress lines, the brief and the report. Files
you write for the project follow the project's language.

## Non-negotiables

1. **One version, then the user.** Build a version, show it, and change the next thing only from the
   user's words. No automated art-director rounds, no review/fix loops in Workflow or subagents, no
   self-scored iterations, no re-generating an image because you dislike its look. Why: such loops once
   burnt billions of tokens while their scores never converged — the user decides what reads right.
   Before showing you may fix, once, only a *measurable* miss: a red project test, a mechanical
   texture failure (`check` problems), an impact whose `timing` note says the peak is late, a life
   that differs from the brief, a Codex picture standing in for the effect (rule 3). Never taste.
   When the user gave a reference frame or clip, one more check is allowed before showing: the
   reference's layer inventory against our frame (reference route) — each item present or missing,
   plus measured numbers from `vfx_clip.py compare` (core colour, near-white, clipped share) — and at
   most one fix of what is missing or measurably off. A check of presence and numbers, not a score
   and not a second round.
2. **Timing before textures.** For a new effect, no Codex image is generated before the rhythm is
   settled on grey primitives and the user has seen it (Step 2). Re-timing a grey ball is cheap; a
   texture drawn for the wrong rhythm is quota spent twice. The one exception is the reference route,
   when the reference clip already fixes the timing (or the effect is a sandbox piece): the brief says
   the block-in is skipped and why.
3. **Codex draws the concept, not the effect.** A Codex image is a reference: it fixes the look —
   palette, character, amount of detail — and the user approves it. It never goes into the game as a
   picture that only appears, fades, scales or moves. The effect is textures as shader data —
   computed by a script, or drawn anew as one data element with the concept as reference, never cut
   from the concept image (a shape mask, a growth order, a crack map) — and a shader or particle
   curves that build its form over time: growth, a spreading edge, cracks, breaking up. Why: a Codex
   sheet of frost "fits, but it is a reference" — pasted on an enemy as sprites it reads as a sticker;
   a static texture faded in by a shader was rejected the same way.
4. **A complaint names its layer.** Before changing anything after feedback, decide which layer is at
   fault (`brief.md`, feedback dictionary). Never regenerate a texture when a curve is the problem.
5. **Project rules beat this skill.** Read them first (Step 0). An effect the project marks approved
   or frozen is changed only when the user names that effect; then compare its reference frames before
   and after and update its guards in the same change. Shared shaders, materials and emitters an
   approved effect uses get the same before/after comparison.
6. **Numbers are labelled.** In briefs and reports, say which timing numbers are sourced and which are
   your heuristics (`timing.md`). Never invent a ratio and present it as a standard.

## Files of this skill

- `timing.md` — the three phases, sourced numbers vs heuristics, phases → Particle System, what the
  clip check measures. Read before Step 1 (or before the change route).
- `brief.md` — brief template, shape and colour rules, bad/good example, feedback template and the
  dictionary from user words to layers and knobs. Read before Step 1 (or before the change route).
- `unity.md` — layers to modules, prefab structure, blend modes and traps, shaders, budget, tilted
  camera, checking on a phone. Read before Step 2 (the block-in already builds particle systems).
- `textures.md` — concept then working textures, compute or draw, prompt rules and templates,
  cleaning, import. Read before Step 3.
- `project-rules-template.md` — the shape of a project's VFX rules file.
- `vfx_tex.py` — textures: `prep`, `check`, `tile`, `noise`, `glow`, `ring`, `ramp`.
- `vfx_clip.py` — clips: `timing` (events, onset, peak, life, peak share, bright time, near-white),
  `sheet` (numbered contact sheet over the effect, optional reference below) and `compare` (a
  reference frame or still beside ours at the same height, zoomed rows, colour and white numbers).

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
«висит», «сделай ярче», a new texture for it) → the change route below. **A frame or clip to match**
(«сделай как на видео», a still from another game) → the reference route below, then Steps 1–7 with
the reference as the brief's source.

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

## Reference route — "make it like this"

The user gives a frame or a clip of an effect to match. The reference is the brief's main source and
the acceptance; a first version that keeps only its outline is rejected even when it is tidy.

1. Save it beside the effect's files (`ref/<name>.png`, a clip as it is) and crop the effect at full
   resolution; crop the part the user talks about (a ring, a core) once more, zoomed.
2. **Inventory of layers.** Write down every element you see, in material words, none merged or
   dropped: "grainy lilac core with flickering specks", "torn front of radial fibres on a thin
   crooked base line", "rainbow only on the torn fragments", "coral puffs burnt away by noise,
   self-shaded", "streaks and shards thrown toward the shooter", "dark haze behind the ring". Per item:
   shape, colour sampled from the reference in hex, blend, when it appears. The number of layers
   comes from here, not from the floor of three to six.
3. Hold the inventory against the cheap tells (`brief.md`): the reference almost never has them, a
   first version almost always does — name the ones you will avoid.
4. The brief (Step 1) takes its personality, palette and layers from the inventory; timing from the
   clip (`timing` on the reference clip) or, for a still, from the effect's neighbours and the user.
   The grey block-in round (Step 2) may be skipped when the reference clip fixes the timing or the
   effect is a sandbox piece — say so in the brief.
5. Before showing: `vfx_clip.py compare <reference> <our clip or frame> <out.png>` — the whole effect,
   plus a `--zoom` row on the part the user named, at the same moment on both sides (Step 5) — and the
   one inventory check of rule 1.
6. Show (Step 6) with the comparison image next to the clips.

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

## Step 3 — Concept, then textures

**The concept.** One Codex image through the `codex-img` skill: the effect at its peak, in the game's
camera, over the thing it hits or covers (template 0 in `textures.md`). An effect whose shape changes
may get a strip of its states instead — start, peak, end. Show it to the user and ask whether the look
is right; the textures wait for this answer, as they waited for the rhythm. Save it beside the brief
(`concept.png` and `concept.prompt.txt`). It is never imported: it is what the textures and the shader
are checked against. A project's approved effect or reference frames can stand in for it. Draw a
concept only when there is a look to agree on — a shape, a pattern, a manner the user has not seen.
A request for a single texture (sparks, a noise, a ring), an effect built only from computed
textures, or a fix to one layer of an existing effect needs none: the brief and the reference clip
are enough, and a concept would cost a Codex image for nothing.

**The working textures.** List every texture the effect needs: name, role (shape mask, noise, ramp,
trail, growth order, crack map), blend mode, size, **how it is made** (`textures.md`, "Compute or
draw") and what the shader or the particle curves do with it over time ("From the concept to the
effect"). A texture with nothing moving it is the concept pasted in — rule 3. Then:

- **Computed** — `vfx_tex.py noise | glow | ring | ramp`. Exact, seamless, power-of-two, instant.
- **Drawn from the concept** — only a shape whose character a script cannot compute (a flame tongue,
  rock chunks, a stylised arc). Write the prompt from the templates in `textures.md` with the approved
  concept as Image 1 and generate through the `codex-img` skill (three or more images: list and cost
  first, wait for a yes). It is drawn as data — one element, flat, unlit, grey or in the palette as
  the project says — not as a finished picture of the effect. For a series, get one anchor approved
  by the user first and pass it with the concept to every later prompt (numbering in `textures.md`).
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
look at a sheet for breakage (wrong place, wrong blend, missing) — not a round of taste fixes. Also
check, frame against frame, that the form changes: a drawn element large enough to read as a drawing
(a patch, a crust, a crown, a splash) that only appears, fades or scales while its drawing stays the
same is a picture, not an effect (rule 3) — give it a shader stage before showing. Sparks and dust
that just fade are fine.

Run the project's VFX tests (all of them, by name filter, not a hand-picked list). Red is fixed before
anything is shown.

## Step 5 — Film and measure

- Capture at a constant rate with fixed seeds — the project's capture tool and rate, else Unity
  Recorder with Playback = Constant at 30 or 60 fps. One cast per clip; start before the effect spawns
  so the first frame is a clean background. Film three views: **close-up slowed down** (×4),
  **the same close-up at real speed**, and **game scale** (the player's camera and resolution), plus a
  dense-fight shot when the effect repeats. A fast effect is judged on the slowed close-up —
  differences the user missed at game scale showed there — but how it *works* is judged at real
  speed, and the user asks for it next. The close-up is rendered once, at four times the clip rate
  (Recorder: Playback = Constant, target 240 fps for 60 fps clips); that file plays at real speed. Both
  close-ups come from it with ffmpeg (`imageio_ffmpeg.get_ffmpeg_exe()`), no new render:
  slowed `ffmpeg -i cap.mp4 -vf "setpts=4*PTS,fps=60" -an slow.mp4`, real speed
  `ffmpeg -stream_loop 3 -i cap.mp4 -vf fps=60 -an real.mp4` (the loop only for a clip shorter than
  ~2 s; `-an` keeps a recorded sound track from outlasting the video; quote the filter, zsh globs `*`).
  When the project's tool already records slowed (time scale 0.25), the real-speed one is
  `-vf "setpts=PTS/4,fps=60" -an` instead.
- `vfx_clip.py timing <clip>` first — on a real-time clip with one cast: the capture itself or the
  game-scale clip, never the slowed close-up (every second reads four times too long) or the looped
  one (it counts four events). The same goes for `sheet`, whose tiles are labelled in seconds. At
  game scale the effect is small in the frame: pass `--roi x,y,w,h` around it. If the effect is
  already on screen in frame 0, use `--baseline last`.
- Then `vfx_clip.py sheet <clip> <out.png>` — by default it spans the effect, not the empty lead-in;
  add `--ref <approved clip>` to put the reference below.
- With a reference to match (a still or a clip): `vfx_clip.py compare <ref> <ours> <out.png>` with
  `--ref-crop` / `--crop` framing the effect alike in both, and `--zoom` on the part in question
  (repeat `--zoom` for more rows). A clip side without a frame gives its peak inside the crop; a still
  is one moment, so give `--frame` for the same moment of ours (the note says when the two sides are
  picked differently). A reference clip that opens on the effect needs `--ref-baseline last` or
  `--ref-frame`; a phone recording `--ref-cfr 30`. Look at the frames it picked before using its
  numbers.
- Compare with the brief in seconds: life, rise, peak share (an impact peaks within the first quarter
  of life; a rise within ~0.1 s counts as instant), `bright_s` (how long the brightest pixels last —
  report it, do not "fix" it: a bright fire body counts too), near-white against the project ceiling
  measured by the project's own tool.

## Step 6 — Show and take feedback

Open the clips — slowed close-up, real-speed close-up, game scale — and the sheet or comparison for
the user (they must see them, not just read about them; pass absolute paths to the opener) and report
in a few lines: what changed since the last version and in which layer, the measured timing against the brief,
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

After each version, in the user's language — also after long English tool output: the absolute
paths of the clips and the sheet or comparison, the measured numbers
against the brief, what changed and which layer, what was not touched, the one question. Do not paste
diffs or describe pixels.
