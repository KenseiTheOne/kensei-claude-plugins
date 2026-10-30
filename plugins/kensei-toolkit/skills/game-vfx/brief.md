# The brief, the feedback, the dictionary

An agent is a talented newcomer without context: "epic" becomes a white core, a fire flower and a
smoke ring. A brief works when it has numbers and bans with reasons instead of adjectives. Studios
(LoL VFX style guide, VFX Apprentice) describe an effect in this order: gameplay meaning and how fast
the player must read it → shape, timing, colour and value → hierarchy (one primary, the rest
secondary) → budget. Riot's priority: (1) carry the gameplay without noise, (2) carry the theme,
(3) delight — a lower item never outranks a higher one.

## Template

Write it in the user's language; the field names below are the checklist.

```
# <Effect> — brief vN
0. Personality: "<what the player sees, 3–8 words>"
1. Meaning: trigger; what it tells (damage / zone / status / telegraph); whose.
   Frequency: <once per N s | N per second> → loudness: quiet / medium / big.
2. Camera and scale: <camera>; size <a–b game units or R>; how many on screen at once.
3. Shape: <ball / ring / cone / column / falling stroke / patch / spiral>; silhouette at game distance.
   Signature (kept at the lowest quality tier): <…>. Body (full tier only): <…>.
4. Storyboard: | t, s | what happens | curve |
   anticipation ≤ __ / peak within the first ¼ of life / dissipation (alpha | dissolve, edge __).
5. Colour: role → hex, alpha, blend (alpha | additive | share of additive); hue shifts from core to edge.
   White ceiling: <project value>. Check over: <backgrounds — enemies, boss, light floor>.
6. Neighbours: <effect X> — differs on axes <shape / motion / colour temperature / scale / signature>.
7. References: Image 1 — take <…> / do NOT take <…>; reference in the project: <clip path>.
8. Bans, each with its reason: "no closed ring outline — rings mean an enemy telegraph"; …
9. Textures: one sheet = one kind of element; background (black | transparent); view (front | top-down);
   what must stay when editing.
10. Build: systems (name → modules and numbers); shader knobs per phase; sorting band; budget.
11. Do not touch: <approved effects, shared shaders>.
12. Acceptance: clip close-up + game scale + dense fight beside the reference; timing numbers;
    project tests; the question "does it read as <personality>?".
```

## Shape and colour rules to apply while writing it

- **Shape before colour.** Judge the silhouette in greyscale: if it does not read without colour,
  colour will not save it.
- **Scale contrast.** Big + medium + small in one effect. Ten particles of one size read as noise.
- **Value, not saturation.** A bright core works only next to a darker or mid-value edge. Tint the core
  toward the element instead of burning it to white.
- **Hue shift from core to edge.** The core is lighter and warmer, the edge darker and drifting to a
  neighbouring hue — fire: yellow → orange → red → dark crimson smoke. Darkening one hue looks dirty.
- **Shape language.** Sharp corners and torn edges say danger and damage; round and soft say heal,
  shield, buff.
- **Frequent is small and quiet, rare is big and earned.** An effect repeated dozens of times a second
  must not cover enemies; a big one has to be earned.

## What made briefs work (from a project in development: user-approved and rejected versions)

- **Personality in one line** before any number: "a small yellow comet", "a dark crimson-black
  boulder falls straight down".
- **Storyboard as a table in seconds**, sizes in game units.
- **Hex per role** (core, body, edge, smoke, light on the ground) and a **white ceiling** — no pure
  #FFFFFF; hot cores tinted toward the element (fire #FFE9A8, cold cores with R ≤ 220).
- **Five axes of difference** — shape, motion, colour temperature, scale, signature — and a line
  "differs from its neighbour". Two effects of one element differ on at least two axes: "10 % bigger,
  two more sparks" is invisible on a phone.
- **A reference game plus a ban list**: water approved on the first try was briefed as "like Diablo IV;
  hugs the ground, knee-high at most; floor tiles visible through it; #1E5A7A, highlights #3FA0C8.
  Banned: Hokusai curled crests, walls of water, royal blue".
- **Ownership of motifs**: rocks belong to one effect only, steam to another, a closed ring to enemy
  telegraphs.
- **A frozen reference** for what is approved; new work is shown beside it.

## What got rejected, and the rule it taught

| Rejected | How it read | Rule |
|---|---|---|
| cartoon water waves, opaque walls | toy-like; hid the enemies | set manner, height and transparency up front, with bans |
| two rounds of shader patterns on a bad cone shape | "still ugly" | patterns do not save a silhouette: fix the shape first |
| a static texture fading in and out on the floor | not a living effect; gold on the floor read as fire cracks | animate the shape; colour temperature must not match another element |
| pillars, a star and arcs around a hit | formed a **face** | ban symmetry and constant speed; check for pareidolia |
| a thin closed ring on the floor | an enemy telegraph | segments with gaps, soft patches without a rim |
| dim orange additive over violet enemies | read as pink | name the blend mode and the backgrounds to check over |
| a flat "splash crown" sprite on a volumetric wave | foreign drawing on a volume | secondary lives in the same volume (drops fly off the crest) |

## Bad and good, same effect

**Bad:** «Сделай крутой мощный взрыв метеора, эпичный, с огнём, дымом и камнями, чтобы чувствовался
вес. Ярче и больше, чем у фаербола.» — no frequency (it hits ~6 times a second), "brighter and
bigger" is a difference in numbers, no timing, hex, bans or acceptance.

**Good (shortened):**

```
0. Personality: a dark crimson-black boulder falls straight down, hits, a column of black smoke rises.
1. Weapon hit, ~6/s → NOT a big effect: the boulder, its shadow and the crater carry it, not flash size.
2. Flash 1.6 R, smaller than Fireball. Up to 12 at once.
4. −0.25…0 s  boulder falls; a soft shadow without a rim grows 0.3 → 1.0 R, alpha 0.2 → 0.6
   0 s        dark flash 0.45 s #FF3B1A → #7A0A12; light core #FFB060 for 0.1 s only
   0 s        4–6 debris, spread ≤ 2 R, life ≤ 0.6 s, weight as a downward force (project rule)
   0–0.8 s    smoke column grows UP, no radial speed
5. Smoke #2B1517 → #140A0B by ALPHA, not additive: dark smoke adds no brightness in a dense fight.
6. Differs from Fireball on 4 axes: stroke from above + crater vs ball; visible fall vs pop;
   crimson-black vs yellow-orange; rocks as signature.
8. No lava cracks — they belong to burning ground. No smoke ring — a ring means a telegraph.
12. "Reads as a falling rock, not a fire explosion?", "shadow visible before the hit?"
```

## Feedback

A useful correction says what is seen and when, how it reads, which layer is at fault, what is
needed instead, and what stays. Claude reads frames, not video, so it points at frame numbers on the
contact sheet (each tile shows the frame number and its time in seconds).

```
Clip vN, frames 34–41: <what is seen>.
Reads as: <"a telegraph ring", "a white dot", "a face">.
Layer at fault: <shape/texture | timing/curves | colour | number of layers | resolution>.
Needed: <measurable — "peak ≤ 0.1 s", "8 segments with gaps">.
Keep: <what is already right>.
```

When the user's words are vague, translate them yourself with the table and say the translation back
in one line before changing anything.

| User says | Layer | Knob |
|---|---|---|
| «висит», hangs | timing | shorter lifetime; Size over Lifetime ease-out |
| «пшик», «вялый», weak, limp | timing | sharper peak; pause before the secondary; turbulent tail, not a calm one (Noise module, or random velocity curves where the project forbids Noise) |
| «всё одной скорости», no weight | timing | different speeds per layer; fast start with braking; compress before rising |
| «плывёт», floats | motion | downward force (or the project's gravity rule); shorter life |
| «выгорает в белое», burns to white | colour / layers | fewer overlapping additive particles; darker core; lower additive share |
| «тает в кашу», melts to mush | shader | narrower dissolve edge; faster threshold |
| «плоско», flat | colour / layers | colour variation; a dark underlay beneath the effect |
| «бумажное», paper-thin | renderer | do not emit stretched particles toward the camera |
| «мыло», blurry | texture | cell resolution, mips, compression block |
| «не читается как X», reads as something else | shape / texture | silhouette first; check in greyscale; compare with the neighbour |
| «каша», noise, no focus | layers | fewer layers; secondary lower in value and opacity; one focal point |
