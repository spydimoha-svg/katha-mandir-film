# Pipeline reference

File formats, the framing arithmetic, and how to extend a project after it is
built.

## Project files

All live in the project root. Each stage reads the ones before it.

| File | Written by | Holds |
| --- | --- | --- |
| `project.json` | `parse_script.py`, then you | paths, title card text, output name, `publish_as`, `reel_titles`, `provenance_box`, `accepted_blur`, `cold_open` |
| `.tmp/km/segments/*.fix` | `build_film.py` | the repair each cached segment was built with, so a corrected repair rebuilds |
| `script.json` | `parse_script.py` | scenes, dialogue, cast and cast_desc, per-scene location, blocks, hooks, prompts |
| `clips.json` | `inventory.py` | clip specs and proposed mappings |
| `edl.json` | **you** | the confirmed scene-to-clip mapping |
| `fixes.json` | **you**, from `qc.py measure` | per-scene repair filter strings |
| `captions.json` | `captions.py scaffold`, English by you | caption text and timings |

Outputs: the film and its `reels/`, `captions/` (subtitles) with
`captions/review/` (Word copies), and `thumbnails/`.

Intermediates live in `.tmp/km/` and are disposable. Cached segments make
rebuilds cheap, so do not delete them casually, but nothing there is precious.

### edl.json

```json
{
  "map": [{"scene": 1, "file": "clip.mp4"}],
  "duplicates": [{"scene": 1, "file": "other_take.mp4", "note": "second take"}],
  "missing_scenes": [11, 22]
}
```

`map` drives everything. One entry per scene; a scene absent from `map` is
simply not in the film. Entries in `duplicates` are ignored by the renderers and
exist so every clip on disk is accounted for.

### fixes.json

```json
{
  "6":  "delogo=x=660:y=252:w=293:h=147:enable='between(t,4.42,6.44)'",
  "29": "crop=1048:590:232:62,scale=1280:720:flags=lanczos",
  "37": "delogo=x=468:y=316:w=227:h=45"
}
```

Any ffmpeg filter chain valid on a 1280x720 frame, including a small graph with
`;` and labels. It runs after the clip has been put on the project spec and
before reframing and the watermark, so boxes are measured in 1280x720
coordinates and stay valid in both the film and the reels.

Label every internal pad with a `km` prefix. `build_segment` wraps a repair in
`[0:v]...[b]` to composite the watermark, so a bare `[a]` or `[b]` inside the
repair collides with the wrapper.

Three shapes of repair are in use, and `qc.py` writes the first two for you:

```json
{
  "29": "crop=1048:590:232:62,scale=1280:720:flags=lanczos",
  "37": "delogo=x=468:y=316:w=227:h=45",
  "19": "split=4[kmMain][kmS1]...;[kmO3]delogo=x=952:y=232:w=34:h=26:enable='...'"
}
```

The sharpness gate compares against the *actual* background of that shot, so it
is not a blanket ban on blurring. A blur over out-of-focus grove scored 1.22 and
passed, because the background there really is that soft; the same technique
over a street with rooftops is what failed. `accepted_blur` in `project.json`
(`{"50": "why"}`) exists as a last resort for a shot with no clean frame at all,
where the control falls back to the surrounding ring, but it has not been needed
yet and a reason is required so it cannot quietly become a way to switch the
gate off.

A fourth is a reframe: `crop=1024:576:128:0,scale=1280:720:flags=lanczos`.
Nothing is interpolated, so it is the only trace-free option when the artifact
sits on a moving actor and a patch is impossible. Crop past the provenance mark
rather than merely past the artifact, so `map_box` returns `gone` and the
watermark stays where it is in every other scene.

Each built segment carries a `.fix` sidecar holding the repair string it was
encoded with. A segment is stale when that no longer matches, which is what
makes editing `fixes.json` actually rebuild; keying on the source clip alone
meant a corrected repair never reached the file.

The third is a patch repair from `qc.py patch`: it splits the frame, holds one
clean frame as a still, crops patch rectangles out of it and overlays them back,
then delogos whatever is left. Prefer it over anything that blurs. `qc.py
verifyfix` scores every repair for sharpness and `run.py verify` fails on a
repair that turned out to be a blur.

### cold_open

```json
"cold_open": {"9": {"scene": 38, "from": 6.70}}
```

That reel starts `from` seconds into its first scene instead of at zero, which
is how a finale replays the previous part's last line before answering it. Both
`build_reels.py` and `captions.py` read it, so the reel's caption track shifts
with the picture; read by only one of them, the whole track is late by whatever
was trimmed.

`from` is a point in the clip, measured off the **audio**. The caption
scaffold's timings are text lengths spread across the clip, not speech, and on
the fourth project they put the line 0.58s later than it is actually spoken.
Cut inside the silence in front of the line rather than at the line.

The named scene must be the block's first, and must carry no repair: the seek
resets the clip's clock, so a windowed `enable='between(t,..)'` would fire
against the wrong timestamps and put the artifact back while the render still
reports success. `build_reels.py` refuses both rather than rendering them.

The cold open is fingerprinted into the segment's `.fix` sidecar, so editing
`from` makes that segment stale and it rebuilds.

## Reel framing arithmetic

The 191% CapCut scale on a 9:16 canvas resolves to:

```
crop_w = round(1280 / 1.91)        = 670     centre crop of the source width
crop_x = (1280 - 670) / 2          = 305
band_h = round(720 * 1080 / 670)   = 1160    after scaling up to canvas width
pad_y  = (1920 - 1160) / 2         = 380     black bar above and below
```

Verified against an existing 2160x3840 export: content band 2302px, bars 769px,
which is the same ratio. Output is 1080x1920 rather than 4K because the source
is 720p and the crop is 670px wide, so 4K adds file size and no detail.

The bars are used: part label above, captions below. The watermark goes on the
picture, not in a bar, because a logo in a bar is cropped off in one action.

## Encode settings

`kmlib.VENC` and `kmlib.AENC` are the single source of truth. Every segment in
every stage must use them or the concat demuxer cannot stream-copy. Pinned GOP
(`-g 48 -keyint_min 48 -sc_threshold 0`) and a fixed timescale are what make
that work.

Audio gets a 40ms fade at both ends of every segment. Separately generated clips
have discontinuous room tone, and without the fade the joins click.

## Extending a project

**New clips arrive.** Drop them in the clip folder, add rows to `edl.json`, then
rerun `build_film.py` and `build_reels.py`. Segment caching means only the new
scenes encode. If the new scenes complete a previously empty block, that reel
appears on its own.

**A repair changed.** Edit `fixes.json`, then
`build_film.py --scenes N` and `build_reels.py --reels B --force`.

**Renumbering parts.** Set `publish_as` in `project.json`, rebuild those reels,
delete the old outputs. The label is burned in, so renaming files is not enough.

**Different block size.** `parse_script.py --block-size N`. Reel length is
block size times clip length; six 10 second clips lands just over 60 seconds,
which is fine everywhere (Shorts allows 3 minutes, Reels 90 seconds).

## Reel length and completeness

A block missing scenes still builds, just shorter. `build_reels.py` prints which
reels are incomplete. Judge each one: losing a middle beat is often survivable,
losing the hook scene means the reel has no ending. Say which is which rather
than reporting a count.

## Naming the reel files

Block titles are often Tamil, which makes a Tamil filename. That is fine on disk
and awkward in an upload queue, so `project.json` can carry
`"reel_titles": {"1": "YAARUM ENNA NAMBARADHILLA", ...}` and the file is named
from that instead. The burned-in PART label and the publish copy still use the
Tamil, so nothing the viewer sees changes.

## What the numbers looked like on past projects

Useful as a sanity check on a new run.

**Kodai Kalam (84 scenes, first project)**

- 65 clips, 84 scenes in the script, 64 scenes covered, 1 duplicate take
- All clips 1280x720, 24fps, AAC stereo, mostly 10.01s
- Long form 10:41 including a 5s title card
- 12 reels from 14 blocks; two blocks had no footage at all
- 5 clips carried burned-in text; 2 were corner headers, 3 were transient bubbles
- First-pass mapping: 51% exact by ranking, 60% by assignment, 77% within
  ranking-or-assignment. Everything else needed eyes.

**Theru Nikkudhu (80 scenes, third project)**

- 56 clips for 80 scenes, every clip used exactly once, 24 scenes with no
  footage spread across all ten blocks
- Long form 9:13 including the title card; reels 30s to 70s, mostly 5 to 7 of
  their 8 scenes
- 2 clips came back 640x360 and 4s instead of 1280x720 and 10s
- 2 clips carried a speech bubble of garbled Tamil; 1 had a construction crane
  in its last 2.5s
- The first bubble repair shipped as a feathered blur and had to be redone as a
  patch rebuild after the user saw a grey column in the player. That is where
  `qc.py patch` and `qc.py verifyfix` came from
- Blocks 2, 3 and 5 lost their designed hook scene, so those three reels end on
  a beat that was written as a middle
- The first script with no prompt fields at all, so mapping ran off the
  parenthesised directions plus the speaker names

**Kannanin Kadaisi Echarikkai (23 scenes, second project)**

Scripted as "Udaiyappora Karai"; retitled at delivery.

- 23 clips for 23 scenes, one each, nothing missing and nothing spare
- Uneven blocks from the cut list: 3,3,3,3,3,3,5. Reels 30s, and 38s for part 7
  after a scene was regenerated longer
- Long form 3:43 including the title card
- 2 clips carried burned-in romanised subtitles across the actor's chest; both
  repaired with a 1.15x zoom rather than delogo
- Assignment proposed a complete permutation and was right on 21 of 23; the two
  it swapped were a pair of stream-side scenes that only the footage separates
- 1 scene regenerated by the user after delivery and swapped in, which cost one
  segment re-encode, a caption re-scaffold and one reel rebuild

**Kannanum Vinayagarum Vandha Annadhaanam (40 scenes, fourth project)**

- 40 clips for 40 scenes, every clip used exactly once, nothing missing
- Mixed clip lengths for the first time: 36 at 10.01s and 4 at 8.0s, all
  1280x720 24fps. Uniform spec, so nothing needed the spec prefix
- Long form 6:37 including the title card
- Blocks are the user's own nine-part cut, not the parser's: 5,5,5,5,5,5,4,4,3,
  with scene 38 in parts 8 and 9 because part 9 was written to open by
  replaying part 8's last line. Reels 46s, 50s, 40s and 30s
- 3 clips carried a burned-in Tamil subtitle, bottom centre, and `qc.py patch`
  measured all three as unpatchable: 0 of 354 cells static on scene 6, 1 of 180
  on scene 14. One uniform `crop=1088:612:40:0,scale=1280:720` reframe on all
  three, which also drops the provenance mark off the right edge
- Assignment proposed a complete permutation; 23 of 40 rows disagreed with the
  ranking and every one of those needed 4-frame strips to settle. Two pairs
  were separable only by the footage: the two pottery-yard clips and the two
  flower-stall clips, where only one of each pair contains the purchase
- Skin colour settled one mapping on its own. The script locks Kannan as
  unmistakably blue from scene 11, so the clip where he is not blue can only be
  scene 9 or 10
- The first script in the arrow format, and the first whose `verifyfix` run
  failed two good reframes, which is where `qc.kept_rect` came from

Rough cost of a full run at this size: a few minutes of encoding, and most of
the wall clock in reading contact sheets.

A one-to-one clip count is a good sign but not proof: check the contact sheets
anyway. Two clips whose filenames both say "stream" can still be the wrong way
round.
