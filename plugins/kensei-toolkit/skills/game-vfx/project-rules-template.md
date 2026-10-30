# Project VFX rules — template

A project's VFX rules file lives at `docs/vfx-rules.md` (or `.claude/vfx-rules.md`) and is read in
full before any VFX work. It holds what changes with the project's code; the method stays in the
skill. Keep it short and point to the architecture docs for details. Write it in the project's
language. Sections:

```
# VFX rules — <project>

## Where the truth is
- Architecture sections: <doc §…>. Config with effect data: <asset>. Build scripts: <paths>.

## Renderer and limits
- Pipeline and renderer: <URP 3D | URP 2D Renderer>; HDR <on|off>; Depth / Opaque Texture <on|off>;
  post-processing <on|off>. What follows (no soft particles, no Bloom, colours ≤ 1, …).
- Camera: <tilt, measured from vertical or horizontal; FOV>; ground plane <XZ | XY>.

## Shaders and knobs
- Shared particle shader: <name>, blending <…>; stream order <…>.
- Knobs: <Custom1.x — dissolve threshold, …>. Describe phases in these knobs.

## Textures
- Colour or grey; background; who derives alpha / masks (script and what it does to brightness).
- Import settings set by <script>; do not add another postprocessor.

## Bans (each with its reason)
- <forbidden modules — guard test name>
- <palette ceilings — e.g. white share, max channel value — and the tool that measures them>
- <motifs owned by one effect: rocks, steam, closed rings = enemy telegraph>

## Sorting and budget
- Bands / sorting layers and what may sit above the crowd (life, size).
- Quality tiers and what the lowest tier must keep.

## Approved — do not touch
| Effect | Approved | User's words | Reference (clip / frames) | Guard |
|---|---|---|---|---|

## Rejected — and why
| Effect / version | How it read | Rule it taught |
|---|---|---|

## Capture and checks
- Capture tool and settings (fps, seed, scales); measuring scripts; tests to run before showing.
```
