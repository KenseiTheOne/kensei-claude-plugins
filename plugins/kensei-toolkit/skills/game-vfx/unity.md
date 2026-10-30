# Unity — layers to modules, blending, shaders, budget, traps

Everything here is the generic URP default. The project's rules file overrides it (renderer, shared
shader, forbidden modules, axes).

## Layers

| Layer | Job | Typical life | Blend |
|---|---|---|---|
| flash | marks the moment of impact | 0.05–0.1 s | additive |
| primary shape (core) | says what the effect is: ball, column, ring, cone | the main one | alpha or additive |
| secondary | signature and scale: sparks, drops, debris, embers | 0.3–1 s | additive / alpha |
| smoke, dissipation | a soft exit instead of a cut | the longest | alpha |
| ground | ties it to the world: scorch, puddle, light patch | longer than the rest | alpha, flat on the floor |
| distortion, lights | expensive extras | short | only if the phone can afford them |

Primary carries the gameplay: highest and lowest values (most contrast), saturation, opacity, a crisp
silhouette and the focal point. Secondary is lower in value and opacity, simpler, calmer, and never
competes. On a projectile the primary is the leading edge (it deals the damage) and its hot point sits
on the hitbox. "If everything is important, nothing is."

## Modules — how to write them in a brief

| Module | Controls | Write it as |
|---|---|---|
| Main | lifetime, start speed, size, rotation, colour | ranges: "life 0.2–0.4 s" — one number looks mechanical |
| Emission | how many and when | a hit is a Burst: Rate 0, Burst N at t = 0; pulses — Cycles + Interval |
| Shape | where from, where to | "Circle, Arc 360, Burst Spread, 12" = an even star; Randomize Direction breaks a too-even pattern |
| Limit Velocity over Lifetime | braking | the juicy-hit tool: high Start Speed + Drag |
| Velocity / Force over Lifetime | motion and forces | Orbital — vortex; Radial < 0 — pull in; Speed Modifier 1 → 0 — shoot and hang |
| Size / Color over Lifetime | shape over time | name the curve shape: "0 → 1 in the first 15 %, ease-out, then flat" |
| Texture Sheet Animation | frames or random shape | a variations sheet: Start Frame random, frame fixed |
| Custom Data | shader knobs over life | a curve on Custom1.x = the dissolve curve |
| Renderer | how a particle stands in the world | Stretched Billboard — sparks, drops; Mesh — rings and beams as one particle; Pivot — lift a sprite off the floor |

- Group sparks into 3–7 jets with similar directions rather than an even sphere.
- Do not emit stretched particles toward the camera: they turn into dots or paper.
- A shockwave or a beam is one mesh particle animated over life, not a swarm — cheaper on mobile.

A project may restrict these (its own atlas and Sprites-mode Texture Sheet Animation, forbidden
modules, its own axes) — its rules win.

## Prefab structure

- A root and child systems named by role — Flash, Core, Sparks, Smoke, Ground. Play and Stop on the
  root drive all children.
- Every scale inside the prefab is 1, Scaling Mode = **Hierarchy**. The default is Local, and then the
  children do not grow with the root — scaling the effect to a radius silently does nothing.
- Simulation Space: an aura or shield on a character — Local; a trail, smoke, ground marks — World.
- A long effect of variable length (a buff until removed) is three systems — start, loop, end —
  switched by code: Shuriken normalises fades to lifetime, a stretched lifetime breaks the rhythm.

## Blend modes (URP factors, from `BaseShaderGUI.cs`, Unity 6000.0)

| Mode | Blend | For |
|---|---|---|
| Alpha | `SrcAlpha OneMinusSrcAlpha` | smoke, dust, water — whatever covers the background |
| Additive | `SrcAlpha One` | fire, light, sparks; burns out and disappears over light backgrounds; alpha scales the addition |
| Premultiply | `One OneMinusSrcAlpha` | one material for smoke and glow: alpha 0 adds, alpha 1 covers; fewer draw calls |
| Multiply | `DstColor Zero` | shadows, charring, dark magic |

**Trap — Premultiply in URP Particles Unlit.** The manual says the shader multiplies RGB by alpha. In
the 6000.0 code it does not (`_ALPHAPREMULTIPLY_ON` is enabled only with preserve-specular, which the
particles shader lacks); it expects an already premultiplied texture. So:

- fading a particle with alpha only turns it additive at the end of its life — fade colour to black
  together with alpha;
- a texture without alpha draws as a black square in this mode;
- a straight-alpha texture (colour under transparent pixels) gives a light fringe.

## Shaders — what the shader does and what the texture does

If the **topology** changes (puffs unfold, parts fly apart) it is a flipbook or several particles. If
the **fill, colour or outline** changes, one texture plus a shader is enough. A shader gives motion,
disappearance, rings and edges; it does not create a good silhouette — two rounds of shader patterns
on a bad shape stay a bad shape.

| Technique | Core | Expose |
|---|---|---|
| dissolve (erosion) | `a = smoothstep(t − f, t + f, mask)` with t remapped to [−f, 1 + f], so 0 shows all and 1 shows nothing | threshold (from Custom1.x), softness |
| dissolve edge | the difference of two thresholds gives a band ≈ 0.05 wide | width, edge colour |
| directional dissolve | add a gradient (UV.y or height) to the noise | min / max range |
| UV panning | `uv − frac(time × speed)` (frac keeps precision) | speed, tiling |
| noise distortion | `uv += (noise.xy × 2 − 1) × amount`, noise at its own speed for parallax | amount, noise speed |
| gradient map | grey value → colour from a 256×1 ramp; shifting along the ramp "cools" fire | ramp, offset, contrast |
| polar coordinates | UV → (radius, angle): rings, shockwaves, vortices; the texture must tile horizontally | radius, ring width, twirl |
| fresnel / rim | `pow(1 − dot(N, V), p)` for shields, spheres | power 2–8, colour |
| vertex offset | waves, flame tongues on a mesh; only Sample Texture 2D LOD in the vertex stage | amplitude, frequency, mask |
| soft particles | fade near geometry; needs the Depth Texture | fade distance |

Per-particle values reach the shader through **Custom Vertex Streams**: enable streams in the
Renderer, Unity shows where each lands (UV → TEXCOORD0.xy, Custom1.xy → TEXCOORD0.zw, …). Give the
agent the stream order as an explicit table: two streams written to one TEXCOORD break silently.
Soft Particles need the Depth Texture; Camera Fading does not (it uses the fragment's own depth);
Distortion samples the Opaque Texture.

## Mobile budget

- The cost is **overdraw** — the area of overlapping transparent quads — not the particle count. Put
  the budget in the brief: max particles per layer (sparks ≤ 30, smoke ≤ 15, flash 1–3 as a start),
  big translucent quads on screen at once ≤ 2–3, Noise Octaves 1 / Quality Low, shadows and light
  probes off.
- Crop textures to the shape: empty transparent pixels are drawn too.
- Shaders in half precision except positions, UVs and time (fp16 is about twice as fast on Mali);
  ≤ 2–3 texture samples per pixel; pack same-UV masks into channels (generate each mask separately —
  never ask Codex for a "packed RGBA", it paints channels as colour); `pow` → multiplications; a mesh
  ring instead of polar math on a big quad; panning and distance fades in the vertex stage.
- Noise, Trails, Sub Emitters, Limit Velocity, World space and any change from script turn off
  procedural mode — the system simulates even off screen. Fine for short hits, costly for long auras.
- Bloom: in URP, Bloom without High Quality Filtering counts as mobile-friendly, but it needs HDR or
  values over the threshold; with HDR off, colours clamp at 1 and glow is painted into the texture.

## Tilted camera

- Ground decals lie flat on the floor plane; standing sprites face the camera with the pivot lifted,
  so their lower half does not sink into the floor.
- A circle on the floor shrinks vertically by cos(tilt from vertical) — about 0.77 at 40° from the
  vertical, 0.64 at 40° from the horizontal. Check which one your camera is.
- Horizontal / Vertical Billboard and Gravity Modifier assume "up = world Y". A project whose arena
  lies in another plane needs its own frames and forces — see its rules.
- Spread sparks and splashes along the floor or sideways; flying straight at the camera they read as
  dots.

## Checking on a phone

Device Simulator emulates neither performance nor rendering capabilities. Use a Development build with
the Profiler attached; the URP Rendering Debugger opens on the device with a double three-finger tap
(Development builds only; turn off Strip Debug Variants to get all panels). Watch from arm's length at
50–70 % brightness, in the densest fight, on the weakest target GPU, for 10–15 minutes (heat and
throttling); the metric is frame time, not FPS in an empty scene. A clip from the phone:
`adb shell screenrecord --time-limit 30 /sdcard/fx.mp4`, then `adb pull` — and the same `vfx_clip.py`
steps as for an editor clip, with `--cfr 30`: screenrecord writes a frame only when the screen changes,
so its frame rate is variable.
