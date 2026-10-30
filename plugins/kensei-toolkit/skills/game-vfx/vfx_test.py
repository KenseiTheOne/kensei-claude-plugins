#!/usr/bin/env python3
"""Tests for vfx_tex.py and vfx_clip.py on synthetic images and a synthetic clip.

Run with the skill's environment: ~/.cache/game-vfx/.venv/bin/python -I vfx_test.py
Skips (exit 0) when numpy, Pillow or imageio-ffmpeg are missing, so a release check without the
environment does not fail on it.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import numpy as np
    from PIL import Image
    import imageio_ffmpeg
except ImportError:
    print("vfx_test: skipped — numpy, Pillow or imageio-ffmpeg missing")
    sys.exit(0)

import vfx_clip  # noqa: E402
import vfx_tex  # noqa: E402


def run(module, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = module.main(list(argv))
    return code, json.loads(buf.getvalue())


def save_rgba(path, a):
    Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)


def disc(h, w, cy, cx, r):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.clip(1 - np.hypot(x - cx, y - cy) / r, 0, 1)


class TexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_prep_glow_keeps_drawn_colour_and_opaque_alpha(self):
        a = np.zeros((300, 420, 4), np.float32)
        d = disc(300, 420, 150, 210, 90)
        a[..., 0], a[..., 1], a[..., 3] = d, d * 0.5, 1
        a[..., :3] += 0.02  # the model's noisy black
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "glow")
        self.assertEqual(code, 0, out)
        b = vfx_tex.load(self.path("out.png"))
        self.assertEqual(b.shape[:2], (256, 256))
        self.assertGreater(b[..., 3].min(), 0.99, "additive keeps alpha opaque")
        self.assertLess(b[0, :, :3].max(), 0.02, "background back to black")
        c = b[128, 128]
        self.assertAlmostEqual(float(c[1] / c[0]), 0.5, delta=0.05, msg="hue of the drawing survives")

    def test_prep_glow_premul_alpha_is_coverage(self):
        a = np.zeros((200, 200, 4), np.float32)
        a[..., 0] = disc(200, 200, 100, 100, 60)
        a[..., 3] = 1
        save_rgba(self.path("in.png"), a)
        run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "glow", "--blend", "premul")
        b = vfx_tex.load(self.path("out.png"))
        self.assertLess(b[0, 0, 3], 0.02)
        self.assertGreater(b[128, 128, 3], 0.9)
        self.assertTrue((b[..., 0] <= b[..., 3] + 0.02).all(), "premultiplied: colour never above alpha")

    def test_prep_mask_tints_and_stretches_alpha(self):
        a = np.zeros((200, 200, 4), np.float32)
        a[..., :3] = (disc(200, 200, 100, 100, 70) * 0.6)[..., None]  # light grey puff, never white
        a[..., 3] = 1
        save_rgba(self.path("in.png"), a)
        code, _ = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "mask",
                      "--tint", "#2B1517", "--size", "128")
        self.assertEqual(code, 0)
        b = vfx_tex.load(self.path("out.png"))
        self.assertGreater(b[..., 3].max(), 0.95, "alpha reaches 1 after stretching")
        np.testing.assert_allclose(b[64, 64, :3], [0x2B / 255, 0x15 / 255, 0x17 / 255], atol=0.01)

    def test_prep_solid_normalises_opaque_alpha_253(self):
        a = np.zeros((200, 200, 4), np.float32)
        a[50:150, 60:140, :3] = [0.4, 0.3, 0.2]
        a[50:150, 60:140, 3] = 253 / 255
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "solid")
        self.assertEqual(code, 0, out)
        self.assertTrue(any("normalised" in n for n in out["notes"]))
        self.assertGreater(vfx_tex.load(self.path("out.png"))[128, 128, 3], 0.995)

    def test_prep_solid_rejects_opaque_input(self):
        a = np.ones((64, 64, 4), np.float32)
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "solid")
        self.assertEqual(code, 1)
        self.assertIn("transparent", out["error"])

    def test_prep_grid_relays_four_pieces(self):
        a = np.zeros((400, 400, 4), np.float32)
        for cy, cx, r in ((90, 100, 40), (110, 300, 60), (300, 90, 50), (290, 310, 30)):
            a[..., 3] = np.maximum(a[..., 3], (disc(400, 400, cy, cx, r) > 0).astype(np.float32))
        a[..., :3] = 0.5
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "solid",
                        "--grid", "2x2", "--size", "256")
        self.assertEqual(code, 0, out)
        b = vfx_tex.load(self.path("out.png"))
        self.assertEqual(b.shape[:2], (256, 256))
        for cy in (64, 192):
            for cx in (64, 192):
                self.assertGreater(b[cy, cx, 3], 0.9, "each cell holds a centred piece")

    def test_check_flags_border_and_capped_alpha(self):
        a = np.zeros((128, 128, 4), np.float32)
        a[:, :20, :3] = 1
        a[:, :20, 3] = 253 / 255
        save_rgba(self.path("in.png"), a)
        _, out = run(vfx_tex, "check", self.path("in.png"), "--expect", "alpha")
        self.assertTrue(out["touches_border"])
        self.assertEqual(out["alpha_max"], 253)
        self.assertTrue(any("253" in p for p in out["problems"]))

    def test_tile_removes_seam(self):
        g = np.linspace(0, 1, 256, dtype=np.float32)[None, :].repeat(256, 0)
        a = np.dstack([g, g, g, np.ones_like(g)])
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "tile", self.path("in.png"), self.path("out.png"), "--axis", "x")
        self.assertEqual(code, 0)
        self.assertGreater(out["seam_x_before"], 50)
        self.assertLess(out["seam_x_after"], 2)

    def test_noise_is_seamless(self):
        for kind in ("fbm", "worley"):
            code, out = run(vfx_tex, "noise", self.path(kind + ".png"), "--kind", kind)
            self.assertEqual(code, 0)
            self.assertLess(max(out["seam_x"], out["seam_y"]), 2, kind)

    def test_ramp_endpoints(self):
        run(vfx_tex, "ramp", self.path("r.png"), "--stops", "#FF0000@0,#0000FF@1", "--width", "64")
        r = vfx_tex.load(self.path("r.png"))
        np.testing.assert_allclose(r[0, 0, :3], [1, 0, 0], atol=0.01)
        np.testing.assert_allclose(r[0, -1, :3], [0, 0, 1], atol=0.01)

    def test_glow_and_ring_carry_alpha(self):
        for cmd in ("glow", "ring"):
            run(vfx_tex, cmd, self.path(cmd + ".png"))
            im = Image.open(self.path(cmd + ".png"))
            self.assertEqual(im.mode, "RGBA", cmd)
            self.assertEqual(im.getpixel((0, 0))[3], 0, cmd + ": corner transparent, no square")

    def test_load_scales_16_bit(self):
        g = (disc(64, 64, 32, 32, 30) * 65535).astype(np.uint16)
        Image.fromarray(g).save(self.path("g16.png"))
        a = vfx_tex.load(self.path("g16.png"))
        self.assertAlmostEqual(float(a[32, 47, 0]), 0.5, delta=0.05, msg="mid value kept, not clipped to 1")

    def test_strip_keeps_width_and_tiles(self):
        g = np.zeros((200, 600), np.float32)
        g[80:120] = 0.5 + 0.5 * np.sin(np.linspace(0, 6 * np.pi, 600, endpoint=False))[None, :]
        a = np.dstack([g, g, g, np.ones_like(g)])
        save_rgba(self.path("s.png"), a)
        code, out = run(vfx_tex, "prep", self.path("s.png"), self.path("o.png"), "--mode", "glow", "--strip", "512x128")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["size"], [512, 128])
        _, chk = run(vfx_tex, "check", self.path("o.png"), "--expect", "tile")
        self.assertFalse(any("edge" in p for p in chk["problems"]), chk)

    def test_check_tile_flags_strip_with_black_sides(self):
        a = np.zeros((128, 256, 4), np.float32)
        a[40:80, 30:226, :3] = 1
        a[..., 3] = 1
        save_rgba(self.path("p.png"), a)
        _, chk = run(vfx_tex, "check", self.path("p.png"), "--expect", "tile")
        self.assertTrue(any("cannot tile" in p for p in chk["problems"]), chk)

    def test_grid_reports_dropped_pieces_and_masks_neighbours(self):
        a = np.zeros((400, 400, 4), np.float32)
        spots = ((90, 100, 40), (110, 300, 60), (300, 90, 50), (290, 310, 30), (200, 200, 6))
        for cy, cx, r in spots:
            a[..., 3] = np.maximum(a[..., 3], (disc(400, 400, cy, cx, r) > 0).astype(np.float32))
        a[..., :3] = 0.5
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "solid",
                        "--grid", "2x2", "--size", "256")
        self.assertEqual(code, 0, out)
        self.assertTrue(any("dropped 1" in n for n in out["notes"]), out)

    def test_grid_even_cuts_equal_cells(self):
        a = np.zeros((200, 200, 4), np.float32)
        for cy, cx in ((50, 40), (50, 60), (150, 150)):  # a group of two in one cell
            a[..., 3] = np.maximum(a[..., 3], (disc(200, 200, cy, cx, 12) > 0).astype(np.float32))
        a[..., :3] = 0.5
        save_rgba(self.path("in.png"), a)
        code, out = run(vfx_tex, "prep", self.path("in.png"), self.path("out.png"), "--mode", "solid",
                        "--grid", "2x2", "--even", "--size", "256")
        self.assertEqual(code, 0, out)
        self.assertEqual(sum("empty" in n for n in out["notes"]), 2, out)

    def test_output_must_be_png(self):
        code, out = run(vfx_tex, "glow", self.path("g.jpg"))
        self.assertEqual(code, 1)


def write_clip(path, intensity, size=(160, 128), radius=30, fps=30):
    """A grey background with a glowing disc whose brightness and size follow `intensity` per frame."""
    w, h = size
    writer = imageio_ffmpeg.write_frames(path, (w, h), fps=fps, quality=9, macro_block_size=16)
    writer.send(None)
    for v in intensity:
        f = np.full((h, w, 3), 60, np.uint8)
        if v > 0:
            d = disc(h, w, h // 2, w // 2, radius + radius * 0.6 * v)
            f = np.clip(f + (d[..., None] * v * 190), 0, 255).astype(np.uint8)
        writer.send(np.ascontiguousarray(f))
    writer.close()


class ClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.clip = os.path.join(cls.tmp, "fx.mp4")
        intensity = [0.0] * 5 + [0.6, 1.0, 0.9, 0.75] + [0.75 * (1 - k / 22) for k in range(1, 22)] + [0.0] * 10
        write_clip(cls.clip, intensity)

    def test_timing_finds_early_peak(self):
        code, out = run(vfx_clip, "timing", self.clip)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["fps"], 30)
        self.assertEqual(len(out["events"]), 1, out)
        ev = out["events"][0]
        self.assertTrue(4 <= ev["onset"] <= 6, ev)
        self.assertTrue(6 <= ev["peak"] <= 7, ev)
        self.assertLessEqual(ev["peak_share"], 0.25)
        self.assertEqual(ev["notes"], [])

    def test_timing_splits_two_casts_with_lingering_smoke(self):
        clip = os.path.join(self.tmp, "two.mp4")
        curve = [0.0] * 4 + [1.0, 0.8, 0.5, 0.35, 0.3, 0.28, 0.27] + [1.0, 0.8, 0.5, 0.3, 0.2, 0.1] + [0.0] * 6
        write_clip(clip, curve)
        code, out = run(vfx_clip, "timing", clip)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out["events"]), 2, out)
        self.assertTrue(any("2 separate events" in n for n in out["notes"]))

    def test_timing_warns_when_effect_is_on_screen_in_frame_0(self):
        clip = os.path.join(self.tmp, "late.mp4")
        write_clip(clip, [1.0 - k / 14 for k in range(15)] + [0.0] * 3)
        code, out = run(vfx_clip, "timing", clip)
        self.assertEqual(code, 0, out)
        self.assertTrue(any("baseline" in n for n in out["notes"]), out["notes"])
        code, out = run(vfx_clip, "timing", clip, "--baseline", "last")
        self.assertEqual(out["events"][0]["onset"], 0, out)

    def test_timing_small_effect_needs_no_roi(self):
        clip = os.path.join(self.tmp, "small.mp4")
        write_clip(clip, [0.0] * 4 + [1.0, 0.7, 0.4, 0.2] + [0.0] * 4, size=(640, 480), radius=8)
        code, out = run(vfx_clip, "timing", clip)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out["events"]), 1)

    def test_timing_rejects_clip_without_effect(self):
        flat = os.path.join(self.tmp, "flat.mp4")
        writer = imageio_ffmpeg.write_frames(flat, (64, 64), fps=30, macro_block_size=16)
        writer.send(None)
        for _ in range(10):
            writer.send(np.full((64, 64, 3), 60, np.uint8))
        writer.close()
        code, out = run(vfx_clip, "timing", flat)
        self.assertEqual(code, 1)
        self.assertIn("no visible change", out["error"])
        self.assertIn("--roi", out["error"])

    def test_sheet_with_reference(self):
        out_png = os.path.join(self.tmp, "sheet.png")
        code, out = run(vfx_clip, "sheet", self.clip, out_png, "--count", "12", "--cols", "4",
                        "--width", "160", "--ref", self.clip)
        self.assertEqual(code, 0, out)
        img = Image.open(out_png)
        self.assertLessEqual(img.width, 4 * 160 + 3 * 2, "the reference goes below, the sheet stays narrow")
        self.assertEqual(out["tiles"], 12)
        self.assertGreaterEqual(out["frames"][0], 3, "auto range skips the empty lead-in")


if __name__ == "__main__":
    unittest.main(verbosity=1)
