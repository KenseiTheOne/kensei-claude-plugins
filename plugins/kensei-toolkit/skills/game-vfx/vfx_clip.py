#!/usr/bin/env python3
"""vfx_clip — turn an effect clip into something Claude can read: timing numbers, a numbered contact sheet and a
side-by-side comparison with a reference.

  vfx_clip.py timing CLIP [--baseline first|last|IMG] [--roi x,y,w,h] [--start F] [--end F]
                          [--cfr FPS] [--gap 0.15] [--energy]
  vfx_clip.py sheet CLIP OUT.png [--ref CLIP] [--start F] [--every N] [--count 24] [--cols 6] [--width 320]
                                 [--roi x,y,w,h] [--cfr FPS]
  vfx_clip.py compare REF OURS OUT.png [--ref-frame N] [--frame N] [--ref-crop x,y,w,h] [--crop x,y,w,h]
                                       [--zoom fx,fy,fw,fh] ... [--height 480] [--ref-cfr FPS] [--cfr FPS]
                                       [--ref-baseline first|last] [--baseline first|last]

Frame numbers are the clip's own (0-based); at a constant capture rate frame N is N / fps seconds. A clip
from a phone (adb screenrecord) has a variable rate — pass --cfr 30 (or 60) to resample it first.
`timing` compares every frame with a clean background: the first frame by default (record before the
spawn), the last one (`--baseline last`, the effect is gone at the end) or an image. Thresholds are in
seconds. `compare` takes an image or a clip on either side (a clip gives --frame N, else its peak inside the
crop against its first frame, or its last with --baseline last), crops the effect in each, scales both to one
height and adds a row per --zoom (a box in fractions of each crop, so frame the effect alike in both crops);
it reports the core colour (mean of the effect's brightest pixels, 2 % of the crop or half the effect; the
background is the crop's median colour), near-white and clipped shares per side. `sheet --ref` takes the reference's tiles at
the same times, so the two clips may differ in fps. Every command prints one JSON object. Needs numpy, Pillow and imageio-ffmpeg (SKILL.md, Setup).
"""
import argparse
import json
import math
import os
import sys

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import imageio_ffmpeg
except ImportError as error:
    print(json.dumps({"ok": False, "error": "setup", "detail": str(error),
                      "fix": "python3 -m venv ~/.cache/game-vfx/.venv && "
                             "~/.cache/game-vfx/.venv/bin/pip install -q numpy pillow imageio-ffmpeg"}))
    sys.exit(2)

CHANGED = 12          # a pixel counts as changed when a channel differs from the background by more than this
BRIGHT = 215          # luma of a "flash" pixel
PEAK_SHARE_MAX = 0.25  # sourced: fully active within the first quarter of life (LoL VFX style guide)


def open_clip(path, cfr=None):
    """(meta, iterator of HxWx3 uint8 frames); the ffmpeg process stops when the iterator is closed."""
    if not os.path.isfile(path):
        raise ValueError("clip not found: " + path)
    params = ["-vf", "fps=%g" % cfr] if cfr else None
    gen = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24", output_params=params)
    meta = next(gen)
    if cfr:
        meta["fps"] = cfr
    w, h = meta["size"]

    def it():
        try:
            for raw in gen:
                yield np.frombuffer(raw, np.uint8).reshape(h, w, 3)
        finally:
            gen.close()
    return meta, it()


def clip_meta(path, cfr=None):
    gen = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
    meta = next(gen)
    gen.close()
    if cfr:
        meta["fps"] = cfr
    return meta


def parse_roi(text, w, h):
    if not text:
        return 0, 0, w, h
    x, y, rw, rh = (int(v) for v in text.split(","))
    box = max(0, x), max(0, y), min(w, x + rw), min(h, y + rh)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("region %s is outside the %dx%d frame" % (text, w, h))
    return box


def luma(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def frames_in(path, start, end, cfr):
    """Frames start..end of a clip, streamed: a user's reference video can be minutes long."""
    _, it = open_clip(path, cfr)
    try:
        for i, f in enumerate(it):
            if i < start:
                continue
            if end is not None and i > end:
                break
            yield f
    finally:
        it.close()


def measure(path, roi_text=None, baseline="first", start=0, end=None, cfr=None, min_frames=3):
    """Per-frame curves inside the region: energy (area × contrast of the change), changed share,
    bright-pixel count; and near-white share of the whole frame."""
    if not os.path.isfile(path):
        raise ValueError("clip not found: " + path)
    meta = clip_meta(path, cfr)
    w, h = meta["size"]
    x0, y0, x1, y1 = parse_roi(roi_text, w, h)
    step = max(1, (x1 - x0) // 480)  # long or large clips: measure on a strided grid, not every pixel
    base = None
    if baseline == "last":
        for f in frames_in(path, start, end, cfr):
            base = f
    elif baseline != "first":
        base = np.asarray(Image.open(baseline).convert("RGB"))
        if base.shape != (h, w, 3):
            raise ValueError("baseline size %s differs from the clip %s" % (base.shape, (h, w, 3)))
    b = None if base is None else base[y0:y1:step, x0:x1:step].astype(np.int16)
    energy, changed, bright, white = [], [], [], []
    for f in frames_in(path, start, end, cfr):
        cur = f[y0:y1:step, x0:x1:step].astype(np.int16)
        if b is None:
            b = cur
        d = np.abs(cur - b).max(-1)
        mask = d > CHANGED
        energy.append(float((d * mask).mean()))
        changed.append(float(mask.mean()))
        bright.append(int(((luma(cur) > BRIGHT) & mask).sum()))
        full = f[::step * 2, ::step * 2]
        white.append(float((full >= 230).all(-1).mean()))
    if len(energy) < min_frames:
        raise ValueError("fewer than %d frames in the requested range" % min_frames)
    return meta, start, np.array(energy), np.array(changed), np.array(bright), np.array(white)


def events_of(energy, fps, gap_s):
    """Split the curve into events: by quiet gaps (below 5 % of the top for `gap_s`) and by deep dips
    between two humps (a new cast while the last one's smoke still lingers)."""
    top = energy.max()
    active = energy > 0.05 * top
    gap = max(1, int(round(gap_s * fps))) if fps else 3
    spans, i, n = [], 0, len(energy)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j, quiet, k = i, 0, i
        while k < n and quiet < gap:
            if active[k]:
                j, quiet = k, 0
            else:
                quiet += 1
            k += 1
        if j - i >= 1:
            spans.append((i, j))
        i = j + 1
    smooth = np.convolve(energy, np.ones(3) / 3, mode="same")
    out = []
    for a, z in spans:
        cut = a
        for m in range(a + 2, z - 1):
            if not (smooth[m] <= smooth[m - 1] and smooth[m] <= smooth[m + 1]):
                continue
            left, right = smooth[cut:m].max(), smooth[m:z + 1].max()
            if smooth[m] < 0.4 * min(left, right) and right > 0.15 * top:
                out.append((cut, m))
                cut = m + 1
        out.append((cut, z))
    return out


def run_around(curve, centre, level):
    """Length of the contiguous run around `centre` where curve >= level."""
    lo = hi = centre
    while lo > 0 and curve[lo - 1] >= level:
        lo -= 1
    while hi < len(curve) - 1 and curve[hi + 1] >= level:
        hi += 1
    return hi - lo + 1


def cmd_timing(args):
    meta, first, e, changed, bright, white = measure(args.clip, args.roi, args.baseline, args.start, args.end, args.cfr)
    fps = meta.get("fps") or 0
    if changed.max() < 0.0005:
        raise ValueError("no visible change against the baseline. If the effect is small in the frame, pass "
                         "--roi x,y,w,h around it; if it is already on screen in frame 0, use --baseline last "
                         "or record from before the spawn")

    def sec(n):
        return round(n / fps, 3) if fps else None

    top = e.max()
    out_events = []
    for (a, z) in events_of(e, fps, args.gap):
        seg = e[a:z + 1]
        peak = a + int(np.argmax(seg))
        life = max(z - a, 1)
        ev = {"onset": first + a, "peak": first + peak, "end": first + z,
              "life_s": sec(life), "rise_s": sec(peak - a), "peak_share": round((peak - a) / life, 3),
              "body_s": sec(run_around(e, peak, 0.5 * seg.max())),
              "peak_changed_share": round(float(changed[peak]), 4)}
        bseg = bright[a:z + 1]
        if bseg.max() > 0:
            bp = a + int(np.argmax(bseg))
            ev["bright_frame"] = first + bp
            ev["bright_s"] = sec(run_around(bright, bp, 0.5 * bseg.max()))
        else:
            ev["bright_s"] = 0.0
        notes = []
        # a rise within ~0.1 s reads as instant (sourced hit frame) even when a very short effect makes it > 25 %
        if ev["peak_share"] > PEAK_SHARE_MAX and (not fps or (peak - a) / fps > 0.1):
            notes.append("if this is an impact: peak at %.0f %% of life, the guide wants the first quarter"
                         % (ev["peak_share"] * 100))
        ev["notes"] = notes
        out_events.append(ev)

    report = {"ok": True, "frames": len(e), "fps": fps, "baseline": args.baseline,
              "events": out_events,
              "near_white_max": round(float(white.max()), 4),
              "near_white_p95": round(float(np.percentile(white, 95)), 4)}
    notes = []
    if len(out_events) > 1:
        notes.append("%d separate events in the clip — numbers are per event; film one cast per clip for a clean read"
                     % len(out_events))
    if out_events and out_events[-1]["end"] - first >= len(e) - 2:
        if int(np.argmax(e)) >= len(e) - 3:
            notes.append("energy is highest at the very end — either the effect was already visible in the "
                         "baseline frame (try --baseline last) or the clip cuts the effect")
        else:
            notes.append("the effect is still visible in the last frame — the clip may cut its end")
    if fps and abs(fps - round(fps)) > 0.01:
        notes.append("non-integer fps — a phone clip? resample with --cfr")
    notes.append("near-white is the whole frame (any baseline); the project's own white measure is the authority")
    report["notes"] = notes
    if args.energy:
        k = max(1, int(math.ceil(len(e) / 60.0)))
        report["energy_every"] = k
        report["energy"] = [round(float(v) / top, 3) for v in e[::k]]
    return report


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def labelled(img, text, fnt):
    img = img.copy()
    d = ImageDraw.Draw(img)
    box = d.textbbox((0, 0), text, font=fnt)
    d.rectangle((0, 0, box[2] + 10, box[3] + 8), fill=(0, 0, 0))
    d.text((5, 3), text, fill=(255, 255, 255), font=fnt)
    return img


def grid(tiles, cols, gap=2):
    rows = (len(tiles) + cols - 1) // cols
    w, h = tiles[0].size
    sheet = Image.new("RGB", (cols * w + (cols - 1) * gap, rows * h + (rows - 1) * gap), (40, 40, 40))
    for k, t in enumerate(tiles):
        r, c = divmod(k, cols)
        sheet.paste(t, (c * (w + gap), r * (h + gap)))
    return sheet


def pick(path, start, count, every, width, cfr, roi):
    meta, it = open_clip(path, cfr)
    out = []
    for i, f in enumerate(it):
        if i < start or (i - start) % every:
            continue
        if roi:
            x0, y0, x1, y1 = parse_roi(roi, f.shape[1], f.shape[0])
            f = f[y0:y1, x0:x1]
        img = Image.fromarray(np.ascontiguousarray(f))
        if width and img.width != width:
            img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
        out.append((i, img))
        if len(out) >= count:
            break
    it.close()
    return meta, out


def cmd_sheet(args):
    start, every = args.start, args.every
    if start is None or every is None:
        # cover the effect, not the empty lead-in: onset − 2 frames … end, spread over `count` tiles
        meta, first, e, changed, _, _ = measure(args.clip, args.roi, "first", 0, None, args.cfr)
        if changed.max() >= 0.0005:
            spans = events_of(e, meta.get("fps") or 0, 0.15)
            lo, hi = max(0, spans[0][0] - 2), spans[-1][1]
        else:
            lo, hi = 0, len(e) - 1
        if start is None:
            start = lo
        if every is None:
            every = max(1, int(math.ceil((hi - start + 1) / float(args.count))))
    meta, picked = pick(args.clip, start, args.count, every, args.width, args.cfr, args.roi)
    if not picked:
        raise ValueError("no frames in the requested range")
    fps = meta.get("fps") or 0
    fnt = font(max(14, args.width // 14))

    def name(prefix, i, rate):
        return "%s%d  %.2fs" % (prefix, i, i / rate) if rate else prefix + str(i)
    sheet = grid([labelled(img, name("", i, fps), fnt) for i, img in picked], args.cols)
    result = {"ok": True, "out": args.out, "frames": [picked[0][0], picked[-1][0]], "every": every,
              "fps": fps, "tiles": len(picked)}
    if args.ref:
        ref_fps = clip_meta(args.ref, args.cfr).get("fps") or 0
        scale = ref_fps / fps if fps and ref_fps else 1.0  # a 240 fps capture against an approved 60 fps clip
        _, ref = pick(args.ref, int(round(start * scale)), args.count, max(1, int(round(every * scale))),
                      args.width, args.cfr, args.roi)
        if ref:
            ref_sheet = grid([labelled(img, name("REF ", i, ref_fps), fnt) for i, img in ref], args.cols)
            both = Image.new("RGB", (max(sheet.width, ref_sheet.width), sheet.height + ref_sheet.height + 16),
                             (20, 20, 20))
            both.paste(sheet, (0, 0))
            both.paste(ref_sheet, (0, sheet.height + 16))  # reference below: the sheet stays narrow and readable
            sheet = both
            result["ref"] = args.ref
            result["ref_frames"] = [ref[0][0], ref[-1][0]]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sheet.save(args.out)
    result["size"] = list(sheet.size)
    return result


def single_image(path):
    """True for a picture Pillow reads (png, jpg, tga, tif …, a phone's MPO jpeg); a video or an animated gif
    is a clip. An animated WebP is refused: the bundled ffmpeg cannot decode it."""
    try:
        with Image.open(path) as img:
            frames, fmt = getattr(img, "n_frames", 1), img.format
    except (OSError, ValueError):
        return False
    if fmt == "WEBP" and frames > 1:
        raise ValueError("animated WebP: save the frame you mean as PNG, or convert the file to mp4")
    return frames == 1 or fmt == "MPO"


def load_rgb(path):
    """HxWx3 uint8 and notes. 16-bit greyscale is scaled, not clipped; alpha is laid over black (as additive)."""
    with Image.open(path) as img:
        if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            g = np.asarray(img, np.float32)
            g = np.clip(g / (65535.0 if g.max() > 255 else 255.0), 0, 1)
            return np.repeat((g[..., None] * 255 + 0.5).astype(np.uint8), 3, -1), []
        if "A" in img.getbands() or "transparency" in img.info:
            a = np.asarray(img.convert("RGBA"), np.float32) / 255.0
            rgb = a[..., :3] * a[..., 3:]
            return (rgb * 255 + 0.5).astype(np.uint8), ["%s has transparency: measured over black" % path]
        return np.asarray(img.convert("RGB")), []


def still(path, frame, cfr, crop, baseline, tag):
    """(HxWx3 uint8, frame number or None, picked, notes): an image as it is; a clip at `frame`, else at its
    energy peak inside the crop (against the first or the last frame)."""
    if not os.path.isfile(path):
        raise ValueError("not found: " + path)
    if single_image(path):
        rgb, notes = load_rgb(path)
        return rgb, None, False, notes
    notes, picked = [], frame is None
    if picked:
        _, _, e, _, _, _ = measure(path, crop, baseline, 0, None, cfr, min_frames=1)
        frame = int(np.argmax(e))
        if e.max() == 0:
            notes.append("%s: no visible change inside the crop — frame 0 is shown" % tag)
        elif baseline == "first" and len(e) > 2 and e[-1] > 0.5 * e.max():
            flag = "--ref-" if tag == "REF" else "--"
            notes.append("%s: the first and the last frame differ about as much as the peak does — the clip opens "
                         "or ends on the effect, so frame %d may miss it; look at it, give %sframe, or %sbaseline "
                         "last when the effect is on screen in frame 0" % (tag, frame, flag, flag))
    frames = frames_in(path, frame, frame, cfr)
    f = next(frames, None)
    frames.close()
    if f is None:
        raise ValueError("%s has no frame %d" % (path, frame))
    return f.copy(), frame, picked, notes


def colour_stats(rgb):
    y = luma(rgb.astype(np.float32))
    # the brightest pixels of the effect — those unlike the crop's median (its background) — as many as 2 % of
    # the crop or half the effect: a thin line covers less, a dark puff on a light floor gives its own colour,
    # and one white pixel (a damage number) is a small share of the mean, not its owner
    flat = rgb.reshape(-1, 3).astype(np.int16)
    fx = np.flatnonzero(np.abs(flat - np.median(flat, 0)).max(-1) > CHANGED)
    if fx.size == 0:
        fx = np.arange(flat.shape[0])
    k = min(max(1, (fx.size + 1) // 2), max(1, int(math.ceil(0.02 * flat.shape[0]))))  # a thin line: its bright half
    top = fx[np.argpartition(y.ravel()[fx], -k)[-k:]]
    core = flat[top].astype(np.float32).mean(0)
    return {"core_hex": "#%02X%02X%02X" % tuple(int(round(v)) for v in core),
            "near_white": round(float((rgb >= 230).all(-1).mean()), 4),
            "clipped": round(float((rgb >= 254).any(-1).mean()), 4),
            "bright": round(float((y > BRIGHT).mean()), 4)}


def to_height(img, height):
    return img.resize((max(1, round(img.width * height / img.height)), height), Image.LANCZOS)


def zoom_box(text, w, h):
    fx, fy, fw, fh = (float(v) for v in text.split(","))
    if not (0 <= fx < 1 and 0 <= fy < 1 and fw > 0 and fh > 0):
        raise ValueError("--zoom is fx,fy,fw,fh in fractions of the crop: " + text)
    x0, y0 = int(fx * w), int(fy * h)
    return x0, y0, max(x0 + 1, min(w, int((fx + fw) * w))), max(y0 + 1, min(h, int((fy + fh) * h)))


def cmd_compare(args):
    sides, notes = [], []
    for path, frame, crop, cfr, baseline, tag in (
            (args.ref, args.ref_frame, args.ref_crop, args.ref_cfr, args.ref_baseline, "REF"),
            (args.ours, args.frame, args.crop, args.cfr, args.baseline, "OURS")):
        rgb, used, picked, side_notes = still(path, frame, cfr, crop, baseline, tag)
        notes += side_notes
        x0, y0, x1, y1 = parse_roi(crop, rgb.shape[1], rgb.shape[0])
        rgb = np.ascontiguousarray(rgb[y0:y1, x0:x1])
        if rgb.size == 0:
            raise ValueError("empty crop on " + path)
        label = tag if used is None else "%s frame %d" % (tag, used)
        sides.append({"path": path, "frame": used, "picked": picked, "crop": [x0, y0, x1 - x0, y1 - y0],
                      "rgb": rgb, "label": label})
    if sides[0]["picked"] != sides[1]["picked"]:
        auto, fixed = (sides[0], sides[1]) if sides[0]["picked"] else (sides[1], sides[0])
        notes.append("%s is its clip's peak, %s is a still or a frame you gave — the numbers compare two moments "
                     "unless they match; give the frame for the same moment"
                     % (auto["label"], fixed["label"]))
    fnt = font(max(14, args.height // 24))
    rows = [[labelled(to_height(Image.fromarray(s["rgb"]), args.height), s["label"], fnt) for s in sides]]
    for z in args.zoom or []:
        row = []
        for s in sides:
            h, w = s["rgb"].shape[:2]
            zx0, zy0, zx1, zy1 = zoom_box(z, w, h)
            part = Image.fromarray(np.ascontiguousarray(s["rgb"][zy0:zy1, zx0:zx1]))
            row.append(labelled(to_height(part, args.height), s["label"] + " zoom", fnt))
        rows.append(row)
    gap = 8
    width = max(sum(t.width for t in r) + gap for r in rows)
    sheet = Image.new("RGB", (width, len(rows) * args.height + (len(rows) - 1) * gap), (20, 20, 20))
    for k, r in enumerate(rows):
        sheet.paste(r[0], (0, k * (args.height + gap)))
        sheet.paste(r[1], (r[0].width + gap, k * (args.height + gap)))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sheet.save(args.out)
    result = {"ok": True, "out": args.out, "size": list(sheet.size), "rows": len(rows)}
    for s, key in zip(sides, ("ref", "ours")):
        result[key] = {"path": s["path"], "frame": s["frame"], "crop": s["crop"], **colour_stats(s["rgb"])}
    ra, oa = (s["crop"][2] / float(s["crop"][3]) for s in sides)
    if abs(ra - oa) > 0.15 * max(ra, oa):
        notes.append("the crops differ in shape (%.2f vs %.2f) — frame the effect alike, or the zoom rows do not "
                     "show the same part at the same scale" % (ra, oa))
    notes.append("numbers say what is present and how white, not which looks better — that is the user's call")
    result["notes"] = notes
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("timing")
    s.add_argument("clip")
    s.add_argument("--baseline", default="first")
    s.add_argument("--roi")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--end", type=int)
    s.add_argument("--cfr", type=float)
    s.add_argument("--gap", type=float, default=0.15, help="seconds of quiet that split two events")
    s.add_argument("--energy", action="store_true", help="also print the energy curve (≤ 60 points)")

    s = sub.add_parser("sheet")
    s.add_argument("clip")
    s.add_argument("out")
    s.add_argument("--ref")
    s.add_argument("--start", type=int)
    s.add_argument("--every", type=int)
    s.add_argument("--count", type=int, default=24)
    s.add_argument("--cols", type=int, default=6)
    s.add_argument("--width", type=int, default=320)
    s.add_argument("--roi")
    s.add_argument("--cfr", type=float)

    s = sub.add_parser("compare")
    s.add_argument("ref")
    s.add_argument("ours")
    s.add_argument("out")
    s.add_argument("--ref-frame", type=int)
    s.add_argument("--frame", type=int)
    s.add_argument("--ref-crop")
    s.add_argument("--crop")
    s.add_argument("--zoom", action="append", help="fx,fy,fw,fh in fractions of each crop; repeat for more rows")
    s.add_argument("--height", type=int, default=480)
    s.add_argument("--ref-cfr", type=float, help="resample the reference clip (a phone recording) first")
    s.add_argument("--cfr", type=float, help="resample our clip first; --frame counts resampled frames")
    s.add_argument("--ref-baseline", choices=("first", "last"), default="first",
                   help="clean frame for the reference's peak search: last when it opens on the effect")
    s.add_argument("--baseline", choices=("first", "last"), default="first")

    args = p.parse_args(argv)
    try:
        result = globals()["cmd_" + args.cmd](args)
    except (ValueError, OSError, RuntimeError, StopIteration) as error:
        print(json.dumps({"ok": False, "error": str(error) or type(error).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
