---
name: katha-mandir-film
description: >
  Turn a Katha Mandir shooting script plus a folder of Veo/Flow clips into a
  finished long-form film, a full run of vertical reels, sidecar caption files,
  thumbnails and a publish sheet. Use this whenever the user has generated AI
  video clips against a numbered scene script and wants them assembled, edited,
  cut into Shorts or Reels, or quality-checked, and for every kind of follow-up
  on an assembled project: re-cutting reels, renumbering parts, replacing a
  regenerated scene, changing the title or title card, fixing burned-in text or
  garbled on-screen dialogue, covering the Gemini/Flow watermark, moving the
  part label, or regenerating captions. Trigger it even when the user does not
  name the pipeline, for example "combine these videos in script order", "make
  shorts out of my long video", "some parts look smeared", "which scenes am I
  missing", "the Gemini watermark is showing", "YouTube will not accept these
  captions", "there is a speech bubble in this clip", "which scenes still need
  generating", or "I regenerated this clip, swap it in".
---

# Katha Mandir film pipeline

Script plus clips in, finished film and reels out.

Deterministic execution belongs in `scripts/`; judgment belongs to you. Do not
hand-roll ffmpeg for something a bundled script already does. Those scripts
encode measurements and failures that cost real time to rediscover, and
`references/gotchas.md` explains each one.

**Your judgment is needed in exactly four places**: mapping clips to scenes,
spotting artifacts, writing the English captions, and writing the publish copy.
Everything else is mechanical and already solved.

## Run it

`S` is this skill's `scripts/` directory. Run from the project folder.

```bash
python $S/run.py --root . prep       # parse + inventory, then STOPS
python $S/run.py --root . qc         # sweep + scan + caption scaffold, STOPS
python $S/run.py --root . deliver    # film, reels, captions, thumbs, verify
```

Each phase refuses to start until the previous one's decision is on disk. The
individual scripts below still run standalone for follow-up work, which is most
of what you will actually be asked for.

Python needs `imageio_ffmpeg`, `opencv-python`, `numpy`, `scipy`, `pillow`,
`fonttools`, `python-docx`. ffmpeg is not on PATH; `kmlib.FF` finds the bundled
build. Tamil rendering needs PowerShell, which is why this is Windows-only.

## Known constants, already measured

Do not re-derive these. They held across both projects.

| Thing | Value | Where |
| --- | --- | --- |
| Flow's provenance sparkle | 48x48 at x1136-1183, y576-623 | `kmlib.PROVENANCE_BOX` |
| Reel framing | 191% scale, crop 670 of 1280, band 1160, bars 380 | `kmlib.REEL` |
| Source clips | 1280x720, 24fps, AAC stereo, ~10.01s | `kmlib.spec_prefix` |
| Tamil font | Nirmala Text, face 3 of Nirmala.ttc | `kmlib`, `tamil_text` |
| Brand colours, series line | warm near-black, gold, cream, brass | `kmlib.BRAND` |
| Title card | possessive small on line 1, noun phrase large on line 2 | `build_film` |

## Stage 1: parse the script

`parse_script.py` writes `script.json` and scaffolds `project.json`. Read what
it prints. It tells you whether the blocks came from the script's own cut list
or from an assumed block size, and blocks are frequently uneven, 3/3/3/3/3/3/5
on the second project, because the author put a three-shot ending in one reel.

Four script formats are in use and the parser reads all four, but the last two
carry **no field headings at all**: one is a bare screenplay with parenthesised
stage directions, the other an arrow screenplay whose shot description is a
plain English sentence and whose speakers are `name -> addressee`. When either
arrives there is no ACTION to map clips against and no prompt for an unshot
scene, so both are synthesised. Which format arrived is decided once for the
whole document, never per scene: a scene with no dialogue carries no clue of
its own. See gotchas before trusting any of it.

Fill in `project.json`: `title_tamil`, `title_tamil_line2`, `title_latin`,
`series_line`, `film_out`, and `reel_titles` (a Latin name per block, since
block titles are Tamil and a Tamil filename is awkward in an upload queue).
Tamil goes in **logical** order, the way it is typed.

## Stage 2: map clips to scenes

`inventory.py` probes every clip, writes contact sheets to `.tmp/km/`, and
proposes a mapping two ways: per-clip ranking and global assignment.

**This is the work that matters.** Read every contact sheet against the scene's
ACTION line. Ranking was 51% correct, assignment 60%, and rows marked CHECK
where they disagree were right only 37% of the time. A one-to-one clip count is
encouraging but not proof: two clips whose names both say "stream" can still be
the wrong way round.

Write `edl.json`:

```json
{
  "map": [{"scene": 1, "file": "Boy_walking_in_monsoon_rain_...mp4"}],
  "duplicates": [{"scene": 21, "file": "...mp4", "note": "first take, replaced"}],
  "missing_scenes": [11, 22]
}
```

Then **stop and show the user the mapping.** Compact table of scene, file and a
one-line beat, with anything uncertain called out. A wrong map scrambles the
story and you only find out after a full render.

## Stage 3: find and repair burned-in text

Veo draws the prompt into the picture sometimes: corner headers, speech
bubbles, and romanised dialogue as a subtitle across the actor's chest.

```bash
python $S/qc.py --root . sweep    # contact sheets every 1.5s, look at all
python $S/qc.py --root . scan     # persistence map, text comes out legible
```

`scan` exists because per-frame detectors do not work here; a component count
ranked a clip with no text above one that had a subtitle. Read the grid it
writes, ignore its pixel counts.

For each defect:

```bash
python $S/qc.py --root . cuts    "<clip>"
python $S/qc.py --root . profile "<clip>" --box x0,y0,x1,y1
python $S/qc.py --root . measure "<clip>" --scene N --kind bright_text --box x0,y0,x1,y1
python $S/qc.py --root . patch   "<clip>" --scene N --box x0,y0,x1,y1 --window t0,t1
```

**A repair that leaves a smear is a failed repair, not a shipped one.** That is
the whole lesson of this stage; it shipped once and the user saw a grey column
across the picture. Removing the artifact is half the job.

Try the techniques in this order, and `references/gotchas.md` has the worked
examples for each:

1. **`qc.py patch`** rebuilds the area from a clean frame of the same shot. The
   only technique that leaves nothing behind. It measures whether the
   background is static instead of assuming, and emits the whole fix chain.
2. **Zoom-crop** for anything near a frame edge, and the only real answer to
   burned-in subtitles sitting on an actor. `crop=1024:576:128:0,scale=1280:720:flags=lanczos`
   is the house default. **Crop above the provenance mark, not merely above the
   text**: a crop that only clears the text clips that mark and strands it in
   the very corner, where a round logo cannot reach it and the builder says
   `EXPOSED`. Crop past it and `map_box` returns `gone`, the logo stays where
   it sits in every other scene, and the builder says `mark removed by the
   repair`.
3. **delogo**, small and only over smooth ground. It streaks on structure, and
   it ghosts the stems of any letter it covers. If you use it, measure the
   glyph bounds and add 3 or 4 pixels rather than drawing a generous box: a
   tight box on scene 82 scored 0.58 where a loose one scored 0.52. Never split
   one run of text into adjacent boxes, because the interior edges are text and
   delogo interpolates from its own white.
4. **A feathered blur blend**, only when the shot contains no clean frame at
   all and the background is out of focus.

Then check it with numbers, not just by eye:

```bash
python $S/qc.py --root . verifyfix          # every fix, or --scene N
```

It reports bright pixels left behind and a sharpness ratio against a clean
frame. A rebuild scores near 1; a blur scores far below and fails. `run.py
verify` will not pass while any repair fails.

It scores three shapes of repair differently, because one threshold cannot
judge all of them:

- **A paint-over** (patch, delogo, blur) against a clean frame of the same
  shot, or the ring of picture just outside it. Needs 0.55.
- **A reframe** (crop, scale) against `1/zoom**2`, which is roughly what a
  clean upscale costs. Nothing is interpolated, so judging it against 1 fails a
  perfectly good 1.25x crop and pushes you back onto delogo. Pass
  `--box x0,y0,x1,y1` and it also proves the artifact is outside the new frame.
  The control is the part of the source the crop **kept**: measured against the
  whole frame, the discarded strip's own burned-in text counts as detail the
  reframe destroyed, and three identical crops scored 0.29, 0.42 and 0.43
  instead of 0.58, 0.58 and 0.57.
- **A temporal repair** (trim, tpad, loop) on whether the hold is really a hold
  and the clip still comes out the length of its source.

**Also look at every repair at full size**, across the window and just past
each shot cut. At 2x, ideally: the delogo ghosting that the numbers passed at
0.58 is obvious at 2x and invisible at thumbnail size.

## Stage 4: captions, as files

```bash
python $S/captions.py --root . scaffold   # Tamil and timings, English blank
python $S/captions.py --root . check
python $S/captions.py --root . docs
```

Write the `english` field yourself, short, for sense not word for word.

`docs` writes `captions/`: `_Tamil.srt`, `_Tamil.sbv`, `_English.srt`,
`_English.sbv` for the film and every reel, plus `.docx` review copies in
`captions/review/`.

**Captions are never burned into the picture.** One language per file. The
`.docx` is separated because when it sat beside the subtitles it got picked in
an upload dialog and YouTube said "unable to parse selected file", which reads
as broken captions when nothing is wrong. If a platform refuses a `.srt`, hand
over the `.sbv`; that is YouTube's own format.

`build_reels.py --burn-captions` still exists. If you use it, the Tamil goes
through `tamil_text.py`, never `drawtext`.

## Stage 5: build the film

```bash
python $S/build_film.py --root . [--scenes 6,37] [--force]
```

Title card, every mapped scene in order, hard cuts, source audio, watermark.
Segments cache per scene, so a changed repair rebuilds only what moved.

The watermark is placed **to cover Flow's provenance mark**, not at a corner
margin, and follows that mark through each scene's repair. A round logo parked
in the corner leaves the mark showing inside its own transparent corner, which
is exactly how it shipped twice. Any `EXPOSED` line in the output is a build
failure: change the repair, do not ship it.

## Stage 6: build the reels

```bash
python $S/build_reels.py --root . [--reels 1,3] [--force]
```

One reel per block, so no scene splits and each reel ends on its designed hook.
1080x1920, watermark on the picture, part label in the lower bar tight under the
picture edge. That placement survives Instagram's 4:5 grid and YouTube's Shorts
thumbnail; nothing in either bar survives a square 1:1 crop, and putting the
label on the picture is a look to confirm before doing it.

**Cold open.** The one sanctioned exception to whole scenes. A finale usually
wants the previous part's last line replayed first, so set

```json
"cold_open": {"9": {"scene": 38, "from": 6.70}}
```

and that reel starts partway into its first scene. `captions.py` reads the same
entry, so the track shifts with it. **Measure `from` off the audio, never off
the caption scaffold**: those timings are text lengths spread across a clip and
miss real speech by seconds, which opens the finale on half a word. The build
refuses a cold open on a scene that carries a repair, because the seek moves
the timestamps its window fires on. See gotchas.

If a block can never be shot, set `publish_as` in `project.json` so the
published run has no gap, and say plainly that this hides the gap rather than
closing it.

## Stage 7: extras and verification

```bash
python $S/missing_prompts.py --root .    # only if scenes are unshot
python $S/extras.py --root . thumbs
python $S/run.py --root . verify
```

`verify` decodes every output in full, checks spec and audio, and parses every
`.srt` strictly. **A clean decode is not a correct picture.** Then look at the
title card, at every repair, and at one caption bar, at full size.

## Stage 8: publish copy and the title ledger

Write `PUBLISH.md` from `script.json` block titles and hooks:

```markdown
# <Tamil title> (<Romanised>)
## Long form
Title:       <Tamil> | <Romanised> | <English search phrase>
Description: <2-3 sentences, premise and theme, no spoiling the ending>
Tags:        <12-15, mixing Tamil and English>
## Reels
### Part N — <BLOCK TAMIL TITLE>
Title:       <Tamil hook> | <Romanised>
Description: <1-2 sentences ending on that block's cliffhanger>
## Posting order
```

Tamil first, romanisation second, English search phrase third. Mobile truncates
around 40 characters. Every reel description must end on its own hook.

Then update the channel's title ledger at
`Downloads/saashwath youtube/KATHA-MANDIR-TITLES.md`: long-form titles used,
every reel part title, the naming rules, and candidates considered but not used
so they are available for a later film rather than reinvented.

Long-form titles follow one shape: **a god's name in the possessive, then an
evocative noun.** Reel part titles are the opposite: short spoken Tamil, one to
three words, lifted from the dialogue.

## Follow-up work

Most requests are changes to a finished project, not new builds.

| Ask | Do |
| --- | --- |
| "I regenerated this clip" | QC it, copy into the clip folder with a `_v2` name, repoint `edl.json` and move the old take to `duplicates`, re-scaffold captions if the duration changed, rebuild that scene and its reel |
| "change the title" | `project.json` title fields and `film_out`, delete the cached title segment, rebuild, then regenerate captions, thumbnails and zip under the new stem and delete the old-stem files |
| "the watermark is showing" | See gotchas. Do not move the logo by eye |
| "that patch looks blurred / pixelated" | The repair is a blur. Run `qc.py verifyfix` for the number, then redo it with `qc.py patch` and rebuild that scene and its reel |
| "there is English text burned across the bottom" | Flow burned its own subtitles in. Patch will fail if the actor moves, so measure that with `qc.py patch` and expect to reframe. See stage 3 item 2 |
| "captions rejected" | Validate first with `run.py verify`; it is usually the wrong file being picked |
| "renumber the parts" | `publish_as`, rebuild those reels, delete the old outputs; the label is burned in |

## Reporting

State what was built, its runtime and where it is. Then be specific about what
is wrong or uncertain, with timecodes so every claim is checkable: scenes with
no footage and which reels they weaken, mappings you were unsure of, repairs
that are visible if you look, and continuity problems the footage created.

Never call a render verified unless you decoded it and looked at it.

And never describe a repair as done because the artifact is gone. The question
is whether the area still looks like picture. `qc.py verifyfix` gives the
number; a full-size look gives the rest. "It reads as dust" was the wrong call
once and the user saw a grey column across the frame in the player.

## Reference

- `references/gotchas.md` — every failure that cost real time, with its cause.
  Read before repairing artifacts, rendering Tamil, or touching the watermark.
- `references/pipeline.md` — file formats, framing arithmetic, encode settings,
  extending a run, and what the numbers looked like on both projects.
