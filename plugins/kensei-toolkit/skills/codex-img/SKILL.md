---
name: codex-img
description: Generate or edit raster images — icons, sprites, logos, avatars, illustrations, concept art, covers, banners, wallpapers, textures — through the Codex CLI's built-in image tool, on the user's ChatGPT subscription (no API key). Saves each image under the file name you choose, keeps a series in one style from reference images, does transparent PNGs, and runs a whole series from a manifest in the background. Use when the user asks to make a picture — "generate an image", "draw an icon", "make a sprite", "make a logo", "regenerate fire.png", "same style as this one", «сгенерируй картинку», «нарисуй иконку», «сделай спрайт», «нарисуй логотип», «сделай аватарку», «обложку», «перегенерируй», «в том же стиле». Not for diagrams, charts, UI mockups or SVG/vector icons for code — draw those as SVG or an artifact — not for textures of a game visual effect being built (that is game-vfx, which calls this skill itself), and not for looking at, describing or screenshotting an existing image.
argument-hint: "[what to draw] [--out path]"
---

# codex-img — images from Codex, checked before they are handed over

The script runs `codex exec` with a one-line job for the Codex agent ("call the image tool once
with this description"), then does everything deterministic itself: finds the image Codex saved,
copies it to the requested path, checks that a transparent image really has alpha. You write the
prompt, look at every result and decide what to redo.

## What it can and cannot do

- **Can:** one image from a description; up to 5 reference images per call (a style reference for
  a series, or the image to edit — the prompt then says what to change); transparent PNG; a series
  from a manifest; never overwrites a file unless told to. Output is always PNG — `--out` must end
  in `.png` (convert separately if the user needs JPG or WebP).
- **Cannot:** pick the model, quality or size — Codex decides (its source says gpt-image-2; output
  is about 1250 px a side, not always square). No masks or inpainting: an edit redraws the whole
  image from the reference. If the user needs GPT Image 2.5 Flare/Sunburst by name, exact sizes or masks, say that this needs
  the OpenAI API, which this skill deliberately does not use.
- **Cost:** each image is one Codex turn on the user's subscription — about 50–90 s and, measured,
  ~30k input tokens of their Codex quota without references, ~70k with one reference image. There
  is also a separate image limit that can run out while the rest of the quota remains.

## Running the script

macOS / Linux: `python3 "${CLAUDE_SKILL_DIR}/codex_img.py" …` — Windows: `python` or `py -3`
instead of `python3`. It prints one JSON object on stdout. Pass the prompt in single quotes (or
via a quoted heredoc) so `"`, `$` and backticks in it reach Codex as they are.

**Where to save.** Use the path the user gave. Without one: the project's existing asset folder if
it is obvious (`assets/`, `public/images/`, an icon folder next to similar files), otherwise ask.
Never leave a final image in the scratchpad — it is temporary.

```
codex_img.py gen --prompt "<description>" --out <path.png> [--ref <img> ...] [--transparent] [--overwrite]
codex_img.py batch <manifest.json> [--resume]
```

Manifest (relative paths are resolved against the manifest's folder):

```json
{"defaults": {"ref": ["style/fire.png"], "transparent": true},
 "items": [{"out": "icons/water.png", "prompt": "..."},
           {"out": "icons/wind.png", "prompt": "...", "ref": ["extra.png"]}]}
```

`gen` result: `ok`, `out` (where the file actually went — `name.v2.png` when the name was taken),
`size`, `has_alpha`, `secs`, `tokens`, `note`, `thread_id`, `source` (Codex's own copy), and on
failure `error` + `detail` (+ `codex_said`, `exit_code`).

`batch` result: `{"results": [<one gen result per item>], "stopped_reason"?, "hint"?, "warning"?}`
— there is no top-level `ok`. An item with `"skipped": "done earlier"` was not generated in this
run: `--resume` found the file an earlier run of this manifest wrote. It prints `[i/N] …` progress
lines on stderr. Where each item was written is recorded in `<manifest>.codex-img-state.json`
next to the manifest; `--resume` trusts only that record, so it never skips or replaces a file
the batch did not write itself (a user's existing `fire.png` stays, the new image is
`fire.v2.png`) — unless the item sets `"overwrite": true`, which replaces it with or without
`--resume`. Keep the state file with the manifest; if it is unreadable, `--resume` stops with
`bad_manifest` rather than generating everything again. Manifest keys per item or in
`defaults`: `ref`, `transparent`, `overwrite` (true/false only); every `out` must be a distinct
`.png`.

## Steps

0. **Generate only when the user asked for an image.** If a task merely refers to an image that
   does not exist (a missing icon in a project, a placeholder), ask first — "X.png is missing —
   generate it through Codex? ~1.5 min and one image from your Codex limit" — and run only on
   a yes.
1. **Write the prompt yourself.** Do not forward the user's words as they are: name the subject,
   style, composition, colours and background. For a transparent asset add "isolated subject,
   centered" and pass `--transparent`. When the user gave criteria (a style guide, a prompt doc,
   "must read at 32 px"), put what matters into the prompt.
2. **One or two images:** run `gen` with the Bash tool and `timeout: 600000`. The script stops
   Codex itself after 540 s (`--timeout`), so it always answers before Bash gives up.
3. **Three or more images:** first show the user the list (file name → one-line prompt) and the
   cost — "N images, about N × 1.5 min and N × ~70k tokens of your Codex quota with a reference
   (~30k without), plus up to 2 retries per image that fails the check". Run only after they
   confirm. Write the manifest to the scratchpad (or next to the outputs if the user wants it
   kept) and run `batch` with `run_in_background: true` and `timeout: 7200000`; you will be
   notified when it ends. Keep one run to about 40 images (it must finish within those 2 hours);
   split a bigger series into several manifests. If a run is stopped before its final JSON
   appears, rerun the same manifest with `--resume` and Read the outputs.
   For a series, make or pick the style reference first and pass it to every item.
4. **Look at every image** with the Read tool before reporting it. Check it against the request:
   subject, style match with the reference, background. `has_alpha: false` on a transparent
   request comes back as `error: "no_alpha"` — the file is kept; look at it and redo it.
5. **Redo only what fails,** with the same reference and a sharper prompt that names what was
   wrong. At most 2 automatic retries per image; after that show the user what you have and ask.
   Retries never overwrite — the new file is `name.v2.png`; tell the user which version you
   recommend, and replace the original (`--overwrite`) only when they ask.
6. **Report:** the paths, what was redone and why. Do not paste base64 or describe pixels — the
   user can open the files.

## Errors

| `error` | Meaning | What to do |
|---|---|---|
| `setup` | codex missing or older than 0.158 | Show `detail`; the user installs or upgrades Codex CLI and signs in with ChatGPT (`codex login`) |
| `bad_manifest` | the manifest is malformed | Fix the manifest (see `detail`) and rerun |
| `rate_limited` | image limit reached | Stop. Tell the user; `batch … --resume` later skips images this manifest already made and redoes ones that failed their check. Do not retry in a loop |
| `codex_failed` | Codex exited before starting a turn (a rejected flag after a Codex update, auth, crash) | Show `detail` to the user; a batch stops here too. Do not retry blindly |
| `stopped_reason: "codex_failing"` | a batch hit 2 `no_image` results in a row with an `exit_code` — Codex errors mid-turn (auth expired, backend down) | Show the `detail`s to the user; after it is fixed, rerun with `--resume` |
| `no_image` | Codex finished without an image | Read `codex_said` / `detail`: a refusal (rephrase); an auth or backend error after the turn started (`exit_code` is set; show `detail` — a batch stops by itself after 2 in a row); or no image tool — Codex signed in with an API key or on the Free plan, where the built-in tool is not available |
| `no_alpha` | asked for transparent, got an opaque image | Look at it, retry with "transparent background, isolated subject" stressed (counts toward the 2 retries) |
| `timeout after … s` | Codex took too long (it was stopped, with its child processes) | Retry once; if it repeats, tell the user |
| `cannot save: …`, `bad png: …` | the image was made (quota spent) but could not be written, or is not a readable PNG | For `cannot save`, fix the path and copy `source` by hand instead of regenerating; for `bad png`, look at `out` and retry once |
| `internal: …` | a bug in the script | Show it to the user; do not retry blindly |
| anything else | bad input: empty prompt, `--out` not `.png`, reference not found or not png/jpg/webp, more than 5 references | Fix the call; nothing was spent |
