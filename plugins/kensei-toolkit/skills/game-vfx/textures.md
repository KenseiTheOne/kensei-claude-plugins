# Textures — compute or draw, prompts, cleaning, import

A VFX texture is data for a shader, not an illustration. A prompt starts with how the engine will
read the texture, then the shape, then the style. Codex draws only what cannot be computed: an image
costs about 1.5 minutes and 30–70 thousand tokens of the user's Codex quota (codex-img measurements),
while a script makes a noise or a ring exactly and instantly.

## Compute or draw

| Texture | Used for | Make it with |
|---|---|---|
| soft dot, glow, radial gradient | flash core, glow | `vfx_tex.py glow` |
| noise: fbm, Worley | dissolve, distortion, UV scroll | `vfx_tex.py noise` — seamless by construction |
| ramp / LUT | grey → colour | `vfx_tex.py ramp` |
| clean ring, simple cross star | shockwave, spark | `vfx_tex.py ring`; a mesh ring for big rings |
| flame tongue, drop, leaf, ice shard, rock | the particle's shape | **Codex** — the best case |
| smoke puff, dust, "poof" | smoke, appearance | **Codex**, one frame |
| stylised ring, slash arc, sigil, lightning | drawn character | **Codex** |
| trail strip | trails, wind | Codex draws, `vfx_tex.py tile --axis x` makes it seamless |
| flipbook sequence (fire, explosion) | frame animation | **not Codex** — frames do not agree with each other. One frame + shader motion, or Blender / EmberGen / a free pack |
| sheet of variations of one kind (4 rocks, 4 puffs) | random shape per particle | Codex; the script re-lays the grid |

At the GDC 2023 VFX roundtable artists named two useful uses of generative models: restyling a
texture toward a reference and filling missing flipbook frames — not generating effects from scratch.

## Background: black or transparent

- **Glowing, soft and smoky things** (fire, magic, sparks, puffs) are drawn on `pure flat black
  #000000`; the script derives alpha. The model's black is noisy, so the black point is always set by
  script (`prep` does it).
- **Solid shapes with crisp edges** (rocks, shards, leaves) are requested transparent (`codex-img
  --transparent`). gpt-image-2 transparency is a bonus, not a foundation: a preview since August 2026,
  refused by the API from 1 October 2026 in community reports, with "opaque" alpha of 252–254 and a
  grey fringe. `prep --mode solid` normalises alpha and pulls colour into the fringe.
- **Never describe a background in a transparent prompt**: the prompt outranks the transparency flag;
  a described scene, colour or plinth gets drawn.
- An image on black used in plain alpha blending gets a dark fringe. For alpha blending the script
  turns it into "RGB white (or a tint), alpha = shape" (`prep --mode mask`). Unity's Alpha Source =
  From Gray Scale is not the same: it leaves RGB grey, so edges darken twice and smoke goes dirty.
- **Dark smoke is not drawn dark on black** — alpha cannot be recovered from a dark drawing on a
  black background. Draw it light grey on black, extract alpha with `prep --mode mask`, and give the
  dark colour in the engine (Start Color / Color over Lifetime).

## Grey or colour

The industry default is a grey mask plus colour in the engine (gradient map or Color over Lifetime):
one shape serves several elements, and a whole series recolours at once. Gradient maps cost an extra
dependent texture read on mobile; Color over Lifetime is the cheaper way to colour.

A project may do it differently — e.g. a colour atlas where the build script derives a dissolve
order from the drawing's brightness for additive sprites. Then the prompt asks for **bright core,
darker edges, in the palette's colours** (brightness is the dissolve order: dim parts and edges go
first) and never for a separate mask. The same builder may read coverage from the source PNG's alpha
for alpha-blended sprites (smoke, decals, water) — then those are requested **transparent**, and a
black background would become an opaque rectangle. Decide per element, as the project rules say.

## Composition and detail

- **One sheet = one kind of element.** OpenAI's own guide says the models struggle to place elements
  precisely in structured layouts. A 2×2 of variations of one kind is fine; a sheet with cells of
  different sizes (rock + trail + crater) is separate generations.
- `centered, generous empty padding, nothing touches the border` — otherwise the model crops the shape
  and the quad shows a straight cut. The script then crops to the shape: less empty area, less overdraw.
- **View depends on where the sprite lies.** A ground decal is drawn straight from above —
  `orthographic top-down, perfectly circular, not an ellipse`; the tilted camera squashes it, and an
  ellipse in the texture would squash it twice. A standing sprite (flame, smoke) — `flat front view`.
- **Detail by number**: `3 flame tongues`, `5–7 soft lobes`, `ring thickness about 8 % of the
  diameter`. "Fine" and "large" are vague to the model; exact geometry is the script's job anyway.
- Against the "illustration" look: `flat, unlit, no 3D shading, no rim light, no specular`; particles
  have their own lighting. Also `readable at 64 px on a phone screen`.
- No tiling in gpt-image: `seamless tileable` only raises the odds; the script checks and fixes it.

## Series and edits

- Approve one anchor image, then pass it as Image 1 to every later prompt, and copy the same STYLE
  LOCK block word for word into each prompt of the series.
- Number references and give each a role: `Image 1: style anchor — match edge softness and value
  distribution; do NOT copy its shape`. Without the last clause the model clones the silhouette. For
  gpt-image-2 a reference sets style, not pixels.
- An edit redraws the whole image: write `change only X, keep everything else the same` and repeat
  what must stay (palette, view, background, contrast, framing). One change per iteration.
- Keep the prompt and the reference list next to the file (`name.prompt.txt`): there is no seed.

## Prompt templates

Structure:

```
ASSET: <what it is> for a real-time particle shader in a mobile game (Unity), used as <additive sprite / alpha-blended sprite / mask>.
SUBJECT: <shape, number of parts, direction>.
VALUES: <bright core to darker edges; palette hex OR strictly grayscale>.
BACKGROUND: pure flat black #000000   |   (transparent: describe no background at all)
COMPOSITION: square 1:1, centered, <flat front view | orthographic top-down>, generous empty padding, nothing touches the border.
STYLE: <STYLE LOCK — the same block in every prompt of the series>.
EXCLUDE: no text, no letters, no watermark, no frame, no border, no vignette, no background gradient, no drop shadow, no checkerboard, no 3D lighting.
```

**1. Flame tongue** (standing, additive) — `prep --mode glow`

```
ASSET: single flame-tongue sprite for an additive particle in a stylized mobile game; brightness drives the dissolve order (dim parts dissolve first).
SUBJECT: one upward flame tongue with 3 flowing tips, slight S-curve, wide rounded base.
VALUES: hot core #FFE9A8 near the base, body #FFB02E, tips #FF6A1A fading into dark red; never pure white.
BACKGROUND: pure flat black #000000.
COMPOSITION: square 1:1, centered, flat front view, the shape fills about 70% of the height, generous black padding, nothing touches the border.
STYLE: <STYLE LOCK>.
EXCLUDE: no sparks, no smoke, no text, no frame, no vignette, no 3D lighting, no background gradient.
```

For a shape recoloured per element, VALUES becomes `strictly monochrome grayscale; bright core
falling off to dark gray tips`. The fire moves by shader (noise scrolled through UV, dissolve curve),
not by a frame sequence.

**2. Smoke puff** (alpha blend) — `prep --mode mask`, colour in the engine

```
ASSET: single smoke puff sprite for an alpha-blended particle; alpha will be extracted from brightness and the colour is set in the engine.
SUBJECT: one round puff made of 5–7 overlapping soft rounded lobes, denser in the middle.
VALUES: strictly grayscale; light gray dense center, thinner toward the edge; the edges fade completely into black with a soft feathered falloff.
BACKGROUND: pure flat black #000000, perfectly uniform.
COMPOSITION: square 1:1, centered, the puff occupies ~65% of the frame, wide black margin, no part touches the edges.
STYLE: <STYLE LOCK>, no fine noise, no thin wisps.
EXCLUDE: no color, no fire, no background haze, no text, no frame, no hard outline, no drop shadow.
```

Lobes keep the silhouette readable at 64 px; fine wisps turn into mip noise on a phone.

**3. Drawn spark burst** (additive) — `prep --mode glow`

```
ASSET: additive glow sprite for hit sparks in a mobile game particle system.
SUBJECT: a four-pointed star burst: two long thin rays (horizontal and vertical), two shorter diagonal rays, a small round core.
VALUES: core <element-tinted hex, never pure white>, rays thin out and fade smoothly to black toward their tips, faint soft halo around the core.
BACKGROUND: pure flat black #000000.
COMPOSITION: square 1:1, exactly centered, symmetrical, ray tips end well before the border.
STYLE: <STYLE LOCK>, crisp but soft-edged.
EXCLUDE: no lens flare rings, no bokeh, no stars in the background, no text, no frame.
```

`exactly centered` because the particle rotates around the centre. A halo is right for a glow sprite
(it is the falloff) and wrong for a shape mask. A plain symmetric star is faster as a script.

**4. Ground patch** (decal) — `prep --mode glow` or `mask`

```
ASSET: ground decal texture for a damage zone; it lies flat on the floor under a tilted 3/4 game camera.
SUBJECT: one irregular soft patch of <scorched ash | shallow dark water with foam lace>, uneven organic outline with 6–8 lobes, NO closed outline ring.
VALUES: <palette hex>; densest in the middle, the outline dissolves softly into black.
BACKGROUND: pure flat black #000000.
COMPOSITION: orthographic top-down view, not an ellipse, centered, the patch spans about 85% of the image width, square 1:1.
STYLE: <STYLE LOCK>, readable at small size.
EXCLUDE: no perspective, no tilt, no floor tiles, no circular rim, no runes, no text, no frame.
```

A segmented ring instead: SUBJECT `6–8 separate arc segments with clear gaps of at least 10°`.

**5. Slash arc** — `prep --mode glow`

```
ASSET: slash arc texture for a melee swipe, used on a curved mesh or a stretched particle.
SUBJECT: a single crescent sweep spanning about 160 degrees, thick and bright at the leading edge, tapering to a thin fading tail; a few thin speed streaks inside the arc follow its curvature.
VALUES: <palette hex>; brightest at the leading edge, smooth gradient to black along the tail.
BACKGROUND: pure flat black #000000.
COMPOSITION: square 1:1, arc centered, flat front view, generous margin, both ends fade out before the border.
STYLE: <STYLE LOCK>, crisp shape, minimal detail.
EXCLUDE: no weapon, no character, no hand, no text, no frame, no sparks outside the arc.
```

The gradient along the arc lets a threshold reveal and erase it over time. Without "no weapon" the
model likes to add a sword.

**6. Trail strip** (tiles along U) — `prep --strip 512x128` (keeps the full width), then `tile --axis x`, then `check --expect tile`

```
ASSET: horizontally tiling strip texture that scrolls along U on a ribbon or stretched particle.
SUBJECT: a wide horizontal band of 6–10 flowing streaks of different lengths, all running left to right.
VALUES: <palette hex>; brightest along the horizontal center line, fading smoothly to pure black at the top and bottom edges.
BACKGROUND: pure flat black #000000 above and below the band.
COMPOSITION: landscape framing, the band spans the full width edge to edge, left and right edges continue seamlessly into each other; the top and bottom 20% stay black.
STYLE: <STYLE LOCK>, soft edges.
EXCLUDE: no vertical elements, no circles, no text, no frame, no vignette.
```

The only template where the drawing touches the border, and only left and right.

**7. 2×2 variations** (rocks, shards) — transparent, `prep --mode solid --grid 2x2` (`--even` when a
cell holds a group; read the `notes` for dropped pieces)

```
ASSET: sheet of 4 independent shape variations for random selection per particle (NOT an animation sequence).
SUBJECT: four different rock-debris chunks, each a distinct angular silhouette, similar size and level of detail.
LAYOUT: exactly 2 columns × 2 rows, one chunk centered in each quarter, wide empty space between chunks, no chunk crosses into another quarter.
VALUES: <palette hex>, 2–3 tones.
STYLE: same as Image 1 (style anchor), <STYLE LOCK>.
EXCLUDE: no grid lines, no cell borders, no numbers, no labels, no ground shadows, no background.
```

"NOT an animation sequence" removes the frame-consistency demand the model fails at.

**8. Next element of a series**

```
Image 1: approved style anchor — match its edge softness, value distribution (bright core, dark edges), stroke character and level of detail. Do NOT copy its shape.
Image 2: rough silhouette sketch — follow this outline only.
Create: <new element>.
Same pipeline rules as Image 1: pure flat black #000000 background, square 1:1, centered, generous padding, nothing touches the border.
Keep identical to Image 1: brightness range, edge softness, simplicity. Do not redesign the style, do not add detail beyond Image 1.
EXCLUDE: no text, no frame, no vignette, no 3D lighting.
```

Repeat the technical rules every time: when redrawing from a reference the model drops them.

**9. Edit**

```
Image 1: the current version.
Change only: <one thing>.
Keep exactly the same: palette, value range, camera angle, silhouette, framing and padding, background, level of detail.
Do not change saturation, contrast or layout. <If transparent: preserve the transparent background.>
```

## Cleaning — `vfx_tex.py`

| Command | Does |
|---|---|
| `prep IN OUT --mode glow [--blend additive\|premul]` | black point, crop to the shape, pad, square, resize to `--size` (power of two). `additive` (default, URP Additive `SrcAlpha One`): RGB as drawn, A = 255. `premul` (a `One OneMinusSrcAlpha` shader): RGB as drawn, A = brightness — the shader or the particle decides how additive it is |
| `prep IN OUT --mode mask [--tint #hex]` | as above, then RGB = white (or the tint), alpha = brightness — for alpha blending |
| `prep IN OUT --mode solid [--grid 2x2 [--even]]` | for transparent input: alpha normalised (90th percentile of the opaque part → 255), colour pulled into the fringe, crop, pad, resize premultiplied; `--grid` re-lays the largest separate pieces, each masked by its own outline, into an even grid (`--even`: equal cells, for groups); dropped pieces are listed in `notes` |
| `prep IN OUT --mode glow\|mask --strip 512x128` | a trail strip: full width kept (no side crop or padding), cropped and padded vertically only |
| `check IN [--expect black\|alpha\|tile]` | size, alpha stats (share of 0 < A < 250, max A), energy on the 2 px border (touches the edge?), near-white share, colourfulness, seam score |
| `tile IN OUT [--axis x\|xy]` | crossfade with a copy shifted by half — seamless; prints the seam score before and after |
| `noise OUT --kind fbm\|worley` | seamless procedural noise, power of two |
| `glow OUT`, `ring OUT`, `ramp OUT --stops "#hex@0,#hex@1"` | procedural soft dot, clean ring, colour ramp |

Resize happens premultiplied; otherwise the filter mixes the black of transparent pixels into the
edge and leaves a dark fringe. Final sizes: 64–128 px for small particles, 256–512 for main
elements, an atlas up to 1024–2048.

## Import in Unity

| Texture | sRGB | Alpha Is Transparency | Wrap | Mips |
|---|---|---|---|---|
| colour sprite / colour atlas | on | off when alpha holds a mask or the background is black (dilation would light up the black) | Clamp | on |
| greyscale mask, noise, flow map, packed channels | **off** (data: otherwise dissolve runs non-linearly) | off | Clamp for a shape, Repeat for a noise | on |
| colour ramp | **on** | — | **Clamp** (Repeat gives bright dots at the end) | off |
| flipbook atlas | by content | as above | Clamp | careful: small mips bleed neighbouring frames — keep a margin per cell |

Mobile: ASTC; every ASTC block is 128 bits whatever the channel count, so packing masks into RGBA
saves samplers, not bits per channel. Read/Write off (it doubles memory). A project's own import
setup (a postprocessor or a setup script) wins: never add a second set of import rules beside it.
