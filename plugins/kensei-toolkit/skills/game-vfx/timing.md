# Timing — three phases, numbers, curves, the clip check

Every effect has the same three phases. The brief writes them as a storyboard in seconds; the
assembly turns them into curves; the clip check measures them back from the pixels.

| Phase | What happens | Curve |
|---|---|---|
| Anticipation (wind-up, cast, telegraph) | compression, gathering, a warning | slow → fast |
| Impact (pop, climax) | flash, peak of size, value, saturation and contrast | sharp, 1–3 frames |
| Dissipation (fall-off, erosion) | break-up, smoke, embers, cooling | fast → slow, low contrast |

Anticipation length is a gameplay decision: an enemy telegraph needs time to react (an AoE warning
of 0.5–2 s in the LoL Meteor example), a hit on a target needs almost none. Dissipation is the
longest part but quiet: low contrast and opacity, so it does not pull the eye from what comes next.

## Numbers — sourced and not

Use these, and say they are sourced:

- **The effect is fully active within the first quarter of its life** — LoL VFX style guide (Riot),
  as summarised by VFX Apprentice. The only exact ratio with a primary source.
- Linear timing is out, for the effect as a whole and for each particle's size and colour curves —
  same guide.
- A flash lasts about 0.1 s (2–3 frames at 30 fps, 5–6 at 60); layers start about 0.07–0.1 s apart,
  not together — realtimevfx breakdown of an Overwatch-style explosion (18 layers). A hit frame of
  about 0.1 s — 80.lv LoL article. Always think in seconds: the same "3 frames" is twice as long at 30
  fps as at 60.
- Find the pauses by voicing the effect's sound: the gaps in the sound are the minimum gaps between
  visual beats — bgolus on realtimevfx. Block the timing in on grey primitives before any texture —
  VFX Apprentice.
- Rhythm carries meaning: faster than roughly a heartbeat (~60 per minute) reads aggressive, slower
  reads friendly (heal, buff) — VFX Apprentice, "The Soul of Effects".
- Secondary that keeps spawning after the hit kills the momentum: stop the spawn, extend the primary
  sparks' life a little, and if a second beat is wanted, start it after a pause — realtimevfx
  feedback threads.

Heuristics — use them as starting values and say they are yours:

- Size over Lifetime for a burst: most of the growth in the first 10–25 % of life (ease-out), then
  nearly flat. Alpha peak inside the first quarter, long quiet tail.
- Motion of a burst: high Start Speed with strong drag, so particles shoot out and nearly stop, then
  dissolve once stopped.
- Frequent effects (dozens per second) short and small — under ~0.3 s; rare earned ones larger and
  longer.

**Do not use** "10 % / 10 % / 80 %" or "80 % of the impact is felt before the hit": they appear only
in catalogues of AI skills, with no primary source. An agent handed such a number fits the effect to
it with confidence.

## Phases → Particle System

| Brief says | Set |
|---|---|
| a burst at the hit | Emission Rate 0, one Burst at t = 0 (or at the hit's delay) |
| layers start apart | Start Delay per child system, 0.07–0.1 s apart |
| fast out, then stop | high Start Speed + Limit Velocity over Lifetime (Drag / Dampen); or Speed Modifier curve 1 → 0 |
| pops up, settles | Size over Lifetime: 0 → overshoot (~1.1) in the first 10–25 %, back to 1, flat |
| peak early, quiet tail | Color over Lifetime alpha: up within the first quarter, long fade; with premultiplied blending fade colour to black together with alpha |
| dissolves, not fades | a dissolve-threshold curve on the shader knob (Custom Data → Custom1.x) held at 0, then rising to 1 by the end |
| variation | every Start value as Random Between Two Constants; Flip; random Start Rotation |
| lasts as long as a buff | three systems — start, loop, end — switched by code; never stretch one lifetime (Shuriken normalises fades to lifetime) |

Order of building layers: primary shape → flash and light → ground layer (the main carrier of a zone
under a tilted camera) → secondary (sparks, debris) → smoke and dissipation → distortion and other
expensive extras only if the effect does not read without them. Add one layer at a time and look.

## What the clip check measures

`vfx_clip.py timing` compares every frame with a clean background — the first frame (record before
the spawn), the last one (`--baseline last`, when the effect is already on screen at the start) or an
image — and measures, from the pixels, inside `--roi` if given:

- **energy** — area × contrast of the change. The curve is split into **events**: by quiet gaps, and
  by a deep dip between two humps (a second cast while the first one's smoke lingers). A clip with
  several events gets a note: film one cast per clip.
- per event: `onset`, `peak`, `end` (frames), `life_s`, `rise_s`, `peak_share` = rise / life,
  `body_s` (how long energy stays above half its peak);
- `bright_s` — how long the count of very bright pixels stays above half its maximum. It includes a
  bright fire body, not only the flash, so it is reported, never auto-fixed;
- `near_white_max`, `near_white_p95` — share of pixels with all channels ≥ 230 over the whole frame.
  The project's own white measure is the authority.

Notes appear only for what is safe to act on: an impact whose peak comes after the first quarter of
its life *and* after more than 0.1 s; several events in one clip; an effect cut at the end or already
visible in the baseline frame. A game-scale clip needs `--roi` around the effect (it is a few percent
of the frame); without it, small effects still register but neighbours mix in. A loop (aura, zone)
has no single peak — read its sheet and check the period. A phone recording has a variable frame rate:
pass `--cfr 30` (or 60).
