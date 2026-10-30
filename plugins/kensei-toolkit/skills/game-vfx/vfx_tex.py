#!/usr/bin/env python3
"""vfx_tex — clean generated VFX textures and compute the ones a script makes better.

  vfx_tex.py prep IN OUT --mode glow|mask|solid [--blend additive|premul] [--tint #hex]
                         [--size 256] [--pad 0.06] [--black 0.03] [--grid CxR [--even]] [--strip WxH] [--fade]
  vfx_tex.py check IN [--expect black|alpha|tile]
  vfx_tex.py tile IN OUT [--axis x|xy]
  vfx_tex.py noise OUT [--kind fbm|worley] [--size 256] [--cells 8] [--octaves 5] [--seed 0]
  vfx_tex.py glow OUT [--size 128] [--power 2]
  vfx_tex.py ring OUT [--size 256] [--radius 0.4] [--width 0.08] [--soft 0.03]
  vfx_tex.py ramp OUT --stops "#FFE9A8@0,#FF6A1A@0.6,#7A0A12@1" [--width 256]

Every command prints one JSON object. Needs numpy and Pillow (see SKILL.md, Setup).
"""
import argparse
import json
import os
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError as error:  # the cached venv is missing
    print(json.dumps({"ok": False, "error": "setup", "detail": str(error),
                      "fix": "python3 -m venv ~/.cache/game-vfx/.venv && "
                             "~/.cache/game-vfx/.venv/bin/pip install -q numpy pillow imageio-ffmpeg"}))
    sys.exit(2)


# ---------- io ----------

def load(path):
    """RGBA float32 in 0..1. 16-bit and float greyscale (Blender, EmberGen exports) are scaled, not clipped."""
    im = Image.open(path)
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        g = np.asarray(im, np.float32)
        g = g / (65535.0 if g.max() > 255 else 255.0)
        g = np.clip(g, 0.0, 1.0)
        return np.dstack([g, g, g, np.ones_like(g)])
    return np.asarray(im.convert("RGBA"), np.float32) / 255.0


def save(a, path):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    a = np.clip(a, 0.0, 1.0)
    if a.ndim == 2:
        Image.fromarray((a * 255 + 0.5).astype(np.uint8)).save(path)
    else:
        Image.fromarray((a * 255 + 0.5).astype(np.uint8)).save(path)


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def luma(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def is_pot(n):
    return n > 0 and n & (n - 1) == 0


def hex_rgb(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("colour must be #RRGGBB: " + text)
    return np.array([int(text[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0


# ---------- geometry ----------

def bbox(alpha, thr):
    ys, xs = np.where(alpha > thr)
    if len(xs) == 0:
        return None
    return ys.min(), ys.max() + 1, xs.min(), xs.max() + 1


def resize_premul(a, w, h):
    """Resize straight-alpha RGBA through premultiplied space: no dark fringe from transparent pixels."""
    pm = a.copy()
    pm[..., :3] *= pm[..., 3:4]
    chans = [np.asarray(Image.fromarray(pm[..., i]).resize((w, h), Image.LANCZOS), np.float32)
             for i in range(4)]
    pm = np.clip(np.dstack(chans), 0.0, 1.0)
    al = pm[..., 3:4]
    pm[..., :3] = np.where(al > 1e-4, pm[..., :3] / np.maximum(al, 1e-4), 0.0)
    return np.clip(pm, 0.0, 1.0)


def fit_square(a, size, pad, thr=0.01):
    """Crop to the shape, pad on every side, centre in a square, resize to size x size."""
    box = bbox(a[..., 3], thr)
    if box is None:
        raise ValueError("the image is empty after the black point / alpha threshold")
    y0, y1, x0, x1 = box
    a = a[y0:y1, x0:x1]
    h, w = a.shape[:2]
    side = int(round(max(h, w) * (1 + 2 * pad)))
    out = np.zeros((side, side, 4), np.float32)
    oy, ox = (side - h) // 2, (side - w) // 2
    out[oy:oy + h, ox:ox + w] = a
    return resize_premul(out, size, size)


def edge_fade(a, start=0.82, end=0.98):
    """Guarantee alpha 0 at the quad's edge (radial), so no square shows."""
    h, w = a.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot((x - (w - 1) / 2) / ((w - 1) / 2), (y - (h - 1) / 2) / ((h - 1) / 2))
    out = a.copy()
    out[..., 3] *= 1 - smoothstep(start, end, r)
    return out


def bleed_colour(a, steps=8):
    """Pull colour from opaque pixels outward into semi-transparent and empty ones (defringe)."""
    rgb, al = a[..., :3].copy(), a[..., 3]
    known = al > 0.9
    if not known.any():
        return a
    acc = rgb * known[..., None]
    weight = known.astype(np.float32)
    for _ in range(steps):
        nb = sum(np.roll(np.roll(acc, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        nw = sum(np.roll(np.roll(weight, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        grow = (~known) & (nw > 0)
        acc[grow] = nb[grow]
        weight[grow] = nw[grow]
        known = known | grow
    filled = np.where(weight[..., None] > 0, acc / np.maximum(weight[..., None], 1e-6), rgb)
    out = a.copy()
    soft = al < 0.9
    out[..., :3][soft] = filled[soft]
    return out


def label_pieces(mask):
    """4-connected components without scipy; returns a label map and the count."""
    h, w = mask.shape
    labels = np.zeros((h, w), np.int32)
    count = 0
    for sy, sx in zip(*np.where(mask)):
        if labels[sy, sx]:
            continue
        count += 1
        stack = [(sy, sx)]
        labels[sy, sx] = count
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = count
                    stack.append((ny, nx))
    return labels, count


def regrid(a, cols, rows, size, pad, even, notes, thr=0.05):
    """Re-lay a sheet into an even grid. `even`: cut the image into equal cells (a cell may hold a group).
    Otherwise: take the largest cols*rows separate pieces, each masked by its own outline (no cut-off bits
    of neighbours), in reading order."""
    cell = size // max(cols, rows) if size else 256
    sheet = np.zeros((rows * cell, cols * cell, 4), np.float32)
    h, w = a.shape[:2]
    if even:
        for r in range(rows):
            for c in range(cols):
                part = a[r * h // rows:(r + 1) * h // rows, c * w // cols:(c + 1) * w // cols]
                if bbox(part[..., 3], 0.01) is None:
                    notes.append("cell %d,%d is empty" % (c, r))
                    continue
                sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = fit_square(part, cell, pad)
        return sheet
    k4 = 4
    small = np.asarray(Image.fromarray(a[..., 3]).resize(
        (max(1, w // k4), max(1, h // k4)), Image.BILINEAR), np.float32) > thr
    labels, count = label_pieces(small)
    need = cols * rows
    if count < need:
        raise ValueError("found %d separate pieces, the grid needs %d (try --even for groups)" % (count, need))
    if count > need:
        notes.append("found %d pieces, took the %d largest, dropped %d — check the sheet, or use --even"
                     % (count, need, count - need))
    sizes = sorted(((int((labels == i).sum()), i) for i in range(1, count + 1)), reverse=True)[:need]
    full = np.kron(labels, np.ones((k4, k4), np.int32))
    full = np.pad(full, ((0, max(0, h - full.shape[0])), (0, max(0, w - full.shape[1]))), mode="edge")[:h, :w]
    pieces = []
    for _, i in sizes:
        own = full == i
        grown = own.copy()  # grow by a few pixels: the coarse map is 4x smaller than the image
        for _ in range(6):
            grown = grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0) | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
        masked = a.copy()
        masked[..., 3] *= grown
        ys, xs = np.where(own)
        pieces.append(((ys.min() + ys.max()) / 2, (xs.min() + xs.max()) / 2, masked))
    row_h = h / rows
    pieces.sort(key=lambda p: (int(p[0] // row_h), p[1]))
    for k, (_, _, masked) in enumerate(pieces):
        r, c = divmod(k, cols)
        sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = fit_square(masked, cell, pad)
    return sheet


def fit_strip(a, w, h, pad, thr=0.01):
    """A horizontal strip: keep the full width (it tiles along U), crop and pad only vertically."""
    ys = np.where(a[..., 3].max(1) > thr)[0]
    if len(ys) == 0:
        raise ValueError("the image is empty after the black point / alpha threshold")
    a = a[ys.min():ys.max() + 1]
    extra = int(round(a.shape[0] * pad))
    a = np.pad(a, ((extra, extra), (0, 0), (0, 0)))
    return resize_premul(a, w, h)


# ---------- commands ----------

def cmd_prep(args):
    a = load(args.input)
    notes = []
    if args.mode in ("glow", "mask"):
        rgb = a[..., :3] * a[..., 3:4]  # a transparent input is composited over black first
        rgb = np.clip((rgb - args.black) / (1 - args.black), 0.0, 1.0)  # black point: the model's black is noisy
        if args.mode == "glow":
            # Drawn on black = premultiplied by its own brightness: unmultiply (alpha = max channel), so
            # the premultiplied resize and the final colour × alpha give back exactly the drawn colour.
            level = rgb.max(-1)
            straight = np.where(level[..., None] > 1e-4, rgb / np.maximum(level[..., None], 1e-4), 0.0)
            work = np.dstack([straight, level])
        else:
            level = luma(rgb)
            hi = np.percentile(level[level > 0.01], 99.5) if (level > 0.01).any() else 1.0
            level = np.clip(level / max(hi, 1e-3), 0.0, 1.0)  # stretch to the full range: alpha reaches 1
            tint = hex_rgb(args.tint) if args.tint else np.ones(3, np.float32)
            work = np.dstack([np.broadcast_to(tint, rgb.shape).astype(np.float32), level])
    else:  # solid: transparent input
        al = a[..., 3]
        if al.min() > 0.99:
            raise ValueError("solid mode needs a transparent PNG; this one is fully opaque")
        opaque = al[al > 0.5]
        p90 = float(np.percentile(opaque, 90)) if opaque.size else 1.0
        if p90 < 1.0:
            notes.append("alpha normalised: 90th percentile of the opaque part was %d" % round(p90 * 255))
        al = np.clip(al / max(p90, 1e-3), 0.0, 1.0)
        work = bleed_colour(np.dstack([a[..., :3], al]))

    if args.strip:
        sw, sh = (int(v) for v in args.strip.lower().split("x"))
        out = fit_strip(work, sw, sh, args.pad)
    elif args.grid:
        cols, rows = (int(v) for v in args.grid.lower().split("x"))
        out = regrid(work, cols, rows, args.size, args.pad, args.even, notes)
    else:
        out = fit_square(work, args.size, args.pad)
    if args.fade:
        out = edge_fade(out)
    if args.mode == "glow":
        if args.blend == "additive":
            # URP Additive (SrcAlpha One): colour as drawn on black, alpha opaque.
            out = np.dstack([out[..., :3] * out[..., 3:4], np.ones(out.shape[:2], np.float32)])
        else:
            # premultiplied shader (One OneMinusSrcAlpha): colour stays premultiplied, alpha = coverage.
            out = np.dstack([out[..., :3] * out[..., 3:4], out[..., 3]])
    save(out, args.output)
    if not (is_pot(out.shape[0]) and is_pot(out.shape[1])):
        notes.append("result %dx%d is not a power of two" % (out.shape[1], out.shape[0]))
    return {"ok": True, "out": args.output, "mode": args.mode, "size": list(out.shape[1::-1]), "notes": notes}


def seam_score(g):
    """~1 — no seam; > 2 — a visible seam (edge jump against the image's own pixel-to-pixel change)."""
    return float(np.abs(g[:, 0] - g[:, -1]).mean() / max(np.abs(np.diff(g, axis=1)).mean(), 1e-6))


def cmd_check(args):
    a = load(args.input)
    h, w = a.shape[:2]
    rgb, al = a[..., :3], a[..., 3]
    level = rgb.max(-1) * al
    border = np.concatenate([level[:2].ravel(), level[-2:].ravel(), level[:, :2].ravel(), level[:, -2:].ravel()])
    soft = float(((al > 0) & (al < 250 / 255)).mean())
    near_white = float(((rgb >= 230 / 255).all(-1) & (al > 0.5)).mean())
    colourful = float(np.abs(rgb - rgb.mean(-1, keepdims=True)).max(-1)[al > 0.1].mean()) if (al > 0.1).any() else 0.0
    g = luma(rgb)
    report = {
        "ok": True, "size": [w, h], "power_of_two": is_pot(w) and is_pot(h),
        "has_alpha": bool(al.min() < 0.999),
        "alpha_max": int(round(float(al.max()) * 255)),
        "alpha_soft_share": round(soft, 4),
        "border_energy": round(float(border.mean()), 4),
        "touches_border": bool(border.max() > 0.08),
        "near_white_share": round(near_white, 4),
        "colourfulness": round(colourful, 4),
        "seam_x": round(seam_score(g), 2), "seam_y": round(seam_score(g.T), 2),
    }
    problems = []
    if args.expect == "black" and report["border_energy"] > 2 / 255:
        problems.append("background is not black at the border")
    if args.expect in ("black", "alpha") and report["touches_border"]:
        problems.append("the shape touches the image border")
    if args.expect == "alpha":
        if not report["has_alpha"]:
            problems.append("no transparency")
        elif 245 <= report["alpha_max"] < 255:
            problems.append("opaque alpha stops at %d — normalise (prep --mode solid)" % report["alpha_max"])
    if args.expect == "tile":
        if max(report["seam_x"], report["seam_y"]) > 2:
            problems.append("visible seam — run tile")
        if level[:, 0].max() < 0.02 or level[:, -1].max() < 0.02:
            problems.append("the drawing does not reach the left or right edge — it cannot tile along U")
    if not report["power_of_two"]:
        problems.append("size is not a power of two")
    report["problems"] = problems
    return report


def make_tileable(g, axis):
    h, w = g.shape[:2]
    shift = (h // 2, w // 2) if axis == "xy" else (0, w // 2)
    rolled = np.roll(g, shift, axis=(0, 1))
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    mx = 1 - np.abs(2 * x / (w - 1) - 1)
    my = 1 - np.abs(2 * y / (h - 1) - 1) if axis == "xy" else np.ones_like(mx)
    m = smoothstep(0.0, 0.5, mx * my)
    if g.ndim == 3:
        m = m[..., None]
    return g * m + rolled * (1 - m)


def cmd_tile(args):
    a = load(args.input)
    before = seam_score(luma(a[..., :3]))
    out = make_tileable(a, args.axis)
    save(out, args.output)
    after_x = seam_score(luma(out[..., :3]))
    after_y = seam_score(luma(out[..., :3]).T)
    return {"ok": True, "out": args.output, "seam_x_before": round(before, 2),
            "seam_x_after": round(after_x, 2), "seam_y_after": round(after_y, 2),
            "note": "structured patterns may double in the blend zone — look at a 2x2 tiling"}


def fbm_tileable(size, octaves, seed, base=4):
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size), np.float32)
    amp, total, period = 1.0, 0.0, base
    for _ in range(octaves):
        if period > size:
            break
        lattice = rng.random((period, period)).astype(np.float32)
        t = np.arange(size, dtype=np.float32) * period / size
        i0 = np.floor(t).astype(int)
        f = t - i0
        f = f * f * (3 - 2 * f)
        i1 = (i0 + 1) % period
        i0 = i0 % period
        top = lattice[i0][:, i0] * (1 - f)[None, :] + lattice[i0][:, i1] * f[None, :]
        bot = lattice[i1][:, i0] * (1 - f)[None, :] + lattice[i1][:, i1] * f[None, :]
        out += amp * (top * (1 - f)[:, None] + bot * f[:, None])
        total += amp
        amp *= 0.5
        period *= 2
    out /= total
    return (out - out.min()) / max(out.max() - out.min(), 1e-6)


def worley_tileable(size, cells, seed):
    jitter = np.random.default_rng(seed).random((cells, cells, 2))
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / size * cells
    cx, cy = np.floor(x).astype(int), np.floor(y).astype(int)
    best = np.full((size, size), 1e9, np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            nx, ny = cx + dx, cy + dy
            j = jitter[ny % cells, nx % cells]
            best = np.minimum(best, np.hypot(nx + j[..., 0] - x, ny + j[..., 1] - y))
    return best / best.max()


def cmd_noise(args):
    if args.kind == "fbm":
        g = fbm_tileable(args.size, args.octaves, args.seed)
    else:
        g = worley_tileable(args.size, args.cells, args.seed)
    save(g, args.output)
    return {"ok": True, "out": args.output, "kind": args.kind, "size": args.size,
            "seam_x": round(seam_score(g), 2), "seam_y": round(seam_score(g.T), 2),
            "import": "sRGB off, Wrap Repeat"}


IMPORT_SHAPE = "RGB white, alpha = shape: works for Alpha, Premultiply and Additive; sRGB off, Wrap Clamp"


def as_rgba(g):
    """White RGB, alpha = value: a shape that draws correctly in every blend mode (no black square)."""
    return np.dstack([np.ones_like(g), np.ones_like(g), np.ones_like(g), g])


def radial(size):
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2
    return np.hypot(x - c, y - c) / c


def cmd_glow(args):
    r = radial(args.size)
    g = np.clip(1 - r, 0.0, 1.0) ** args.power
    save(as_rgba(g), args.output)
    return {"ok": True, "out": args.output, "size": args.size, "import": IMPORT_SHAPE}


def cmd_ring(args):
    r = radial(args.size) / 2  # 0 at the centre, 0.5 at the edge midpoints, like UV distance
    d = np.abs(r - args.radius)
    g = 1 - smoothstep(args.width / 2, args.width / 2 + args.soft, d)
    save(as_rgba(g), args.output)
    return {"ok": True, "out": args.output, "size": args.size, "import": IMPORT_SHAPE}


def cmd_ramp(args):
    stops = []
    for part in args.stops.split(","):
        colour, _, pos = part.partition("@")
        stops.append((float(pos), hex_rgb(colour)))
    stops.sort(key=lambda s: s[0])
    xs = np.linspace(0, 1, args.width)
    out = np.zeros((1, args.width, 4), np.float32)
    for ch in range(3):
        out[0, :, ch] = np.interp(xs, [s[0] for s in stops], [s[1][ch] for s in stops])
    out[..., 3] = 1
    save(out, args.output)
    return {"ok": True, "out": args.output, "width": args.width, "import": "sRGB on, Wrap Clamp, no mips"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prep")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--mode", choices=["glow", "mask", "solid"], required=True)
    s.add_argument("--blend", choices=["additive", "premul"], default="additive")
    s.add_argument("--tint")
    s.add_argument("--size", type=int, default=256)
    s.add_argument("--pad", type=float, default=0.06)
    s.add_argument("--black", type=float, default=0.03)
    s.add_argument("--grid")
    s.add_argument("--even", action="store_true", help="with --grid: cut into equal cells instead of separate pieces")
    s.add_argument("--strip", help="WxH, e.g. 512x128: a trail strip — full width kept, no side padding")
    s.add_argument("--fade", action="store_true")

    s = sub.add_parser("check")
    s.add_argument("input")
    s.add_argument("--expect", choices=["black", "alpha", "tile"])

    s = sub.add_parser("tile")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--axis", choices=["x", "xy"], default="xy")

    s = sub.add_parser("noise")
    s.add_argument("output")
    s.add_argument("--kind", choices=["fbm", "worley"], default="fbm")
    s.add_argument("--size", type=int, default=256)
    s.add_argument("--cells", type=int, default=8)
    s.add_argument("--octaves", type=int, default=5)
    s.add_argument("--seed", type=int, default=0)

    s = sub.add_parser("glow")
    s.add_argument("output")
    s.add_argument("--size", type=int, default=128)
    s.add_argument("--power", type=float, default=2.0)

    s = sub.add_parser("ring")
    s.add_argument("output")
    s.add_argument("--size", type=int, default=256)
    s.add_argument("--radius", type=float, default=0.4)
    s.add_argument("--width", type=float, default=0.08)
    s.add_argument("--soft", type=float, default=0.03)

    s = sub.add_parser("ramp")
    s.add_argument("output")
    s.add_argument("--stops", required=True)
    s.add_argument("--width", type=int, default=256)

    args = p.parse_args(argv)
    for name in ("output",):
        value = getattr(args, name, None)
        if value and not value.lower().endswith(".png"):
            print(json.dumps({"ok": False, "error": "output must be a .png"}))
            return 1
    try:
        result = globals()["cmd_" + args.cmd](args)
    except (ValueError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
