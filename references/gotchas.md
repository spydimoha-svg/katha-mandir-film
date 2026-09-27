# Gotchas

Failures that cost real time on the reference project. Each one shipped, or
nearly shipped, before it was caught.

## Tamil text renders wrong and looks plausible

**Never draw Tamil with `drawtext`. Use `tamil_text.render_batch()` and overlay
the PNGs it returns.** This is the single most expensive thing in this skill to
rediscover, and it has now bitten twice in different ways.

**What happens.** ffmpeg's `drawtext` does not shape Tamil at all on this
machine. Its output is byte-identical with `text_shaping` on or off, so harfbuzz
is not running. Two separate failures follow from that:

- Pre-base vowel signs (`ெ ே ை ொ ோ ௌ`) stay on the wrong side of their
  consonant, and a split sign emits a dotted-circle placeholder. `கோடைக் காலம்`
  came out with a dotted circle inside the first letter; `திருவிளையாடல்` came
  out as `திரவிளையாடல்`.
- The ligating signs `ு` (U+0BC1) and `ூ` (U+0BC2) have no standalone glyph, so
  they **vanish completely**. `இருக்கு கூட யாரும்` renders as `இரக்க கூ ட
  யாரம்`. These two signs appear in most Tamil sentences, so this affects nearly
  every caption.

Reordering by hand (`kmlib.tamil_visual`) fixes the first failure and does
nothing for the second. It was enough for the first film's title card only
because that title happened to contain no `ு`. Pillow cannot help either: it is
built without raqm, so it does no complex-script shaping.

**Why it is dangerous.** Text with its vowel signs missing still looks like
Tamil, and still looks like Tamil at thumbnail size. It is obvious to a Tamil
reader at full size, and by then it is in every reel.

**The fix.** `scripts/tamil_text.py` rasterises each line through GDI+, which
shapes via Uniscribe and is correct, then hands back transparent PNGs to
overlay. It wraps and shrinks to fit there too, since only the shaper can
measure what it will actually draw. One PowerShell launch covers a whole build.
`build_film.py` (title card) and `build_reels.py` (captions) both use it.

**Font.** `tamil_text.FONT` is `Nirmala Text`, the same face `kmlib` extracts
from `Nirmala.ttc` (face 3 of 6; freetype only ever loads face 0 of a
collection), so cards stay consistent with the earlier films. `prepare_fonts()`
still supplies the Latin font, and still copies fonts to colon-free, space-free
paths, because a Windows drive letter inside an ffmpeg filtergraph is an
escaping minefield.

**A single-word line used to render as a single letter.** `-split` in PowerShell
returns a bare string rather than an array when the input has one word, and
indexing a string there yields a CHARACTER. So `கண்ணனின்` came out as `க` on a
title card, which reads as a font problem rather than as a bug. Fixed with `@()`
around the split in `render_tamil.ps1`. Worth remembering as a class of failure:
in that language a one-element result is not a one-element array.

**Always read rendered Tamil at full size before shipping it.** Crop the caption
bar out of a finished reel and look at the actual words. Check the title card
too, and check it after any title change, not only on the first build.

## A repair window that crosses a shot cut lands on a face

**What happened.** Scene 6 looked like one shot. It is six. A speech bubble sat
in the shot running frames 106-154, but the repair window was set by eye to end
at 6.7s, which crosses the cut at 6.458s. For those few frames the patch sat on
the grandmother's face in the next shot, smearing it. It shipped, and the user
found it.

**The rule.** Run `qc.py cuts` on any clip before repairing it, and never let a
window cross a cut. `qc.py measure` enforces this automatically: it detects cuts
and clips every span to shot boundaries.

## A box measured off a loose detector covers the actor

**What happened.** Scene 37's burned-in text is 227px wide. A detector that
swallowed the actor's bright cheek into its bounding box reported 330px, so the
repair box covered his face and smeared it for the whole window. Also shipped.

**The rule.** Measure per frame with a detector tuned to the artifact, not to
brightness in general, then look at the numbers before using them. If a measured
box is much wider than the artifact appears in a zoomed frame, the detector is
picking up something else.

## Detectors miss the fade in and fade out

**What happened.** Artifacts fade in and out over a few frames. A window derived
only from frames where the artifact is at full opacity leaves a visible flash at
each end.

**The fix.** Two options, both in use. If the box is never occupied by anything
else across the whole clip (check with `qc.py profile`), run the repair
unwindowed and there is no edge to miss. Otherwise snap the window out to the
enclosing shot, which `qc.py measure` does when the artifact fills most of it.

## delogo smears on detailed backgrounds

delogo interpolates from the box border, so it is invisible over out-of-focus
background or flat night sky, and obvious where the box crosses a hard edge like
a roofline. Scene 29's corner header sat on a roofline and smeared badly; a
1.22x zoom-crop removed the text entirely with no artifact, at the cost of
slightly tighter framing. Prefer a crop when the background has structure.

A burned-in *subtitle* is the worst case for delogo, because it sits centred at
the bottom of frame, which is usually the actor's chest. Both subtitles on the
second project left a clearly visible blur patch on the shirt even with a
correctly measured box. A single `crop=1112:626:84:0,scale=1280:720` (1.15x)
lifts the whole text off the bottom edge, costs 94px of picture nobody misses,
and leaves no artifact at all. Compare the two at full size before choosing;
the zoom won both times.

## Repairing an artifact: try these in this order

**A repair that leaves a smear is a failed repair.** It shipped once. The
bubble was gone, the result was called "reads as dust", and what the viewer
actually saw in the player was a grey column across the picture. Removing the
artifact is half the job; the other half is that the area still looks like
picture afterwards.

The techniques, best first:

**1. Rebuild the area from a clean frame of the same shot.** `qc.py patch`
plans it. This is the only technique that leaves nothing at all behind, because
the replacement pixels are the real pixels from a moment when the artifact was
not there. It needs two things: a clean frame inside the same shot, and a
background that does not move. Both are measurable, and `patch` measures them
rather than assuming.

Three things about that measurement were wrong on the first try and are worth
not rediscovering:

- **Compare blurred frames.** What disqualifies an area is a person moving
  through it, not the pixels differing. Drifting dust, shimmering leaves and
  compression noise all produce large raw deviations while being invisible once
  patched. At sigma 6 they vanish and a displaced head stays as large as it was.
  Raw differencing rejected a perfectly patchable stretch of sky.
- **Drop the frames next to a shot cut.** They are already dissolving into the
  next shot, so a static background measures as moving. Four frames each end.
- **Take the worst clean frame, not a percentile,** and keep the clean frames
  from *after* the window. Those few frames are the only evidence of how far
  anything drifted across it. A percentile throws exactly them away and calls a
  moving actor static, which is how a single rectangle got planned straight
  over a man's head.

Then pack rectangles largest-first. Grouping columns by equal depth produced a
handful of 16px slivers and hit the rectangle limit before reaching the big
patchable middle, so the residual delogo covered nearly the whole artifact -
the blur all over again, arrived at by a different route.

**2. Zoom-crop.** For an artifact near a frame edge, `crop` then `scale` lifts
it out of frame entirely. Costs a little framing and leaves no artifact. The
house default for a bottom subtitle is
`crop=1112:626:12:0,scale=1280:720:flags=lanczos`, which takes Flow's sparkle
with it in the same step.

**3. delogo, small and over smooth ground only.** It interpolates from the box
border, so it is invisible over flat sky and obvious anywhere the box crosses a
hard edge. A tall box streaks vertically. Keep it under about 50px in the
smaller dimension and keep every border on plain background.

**4. A feathered blur blend.** Last resort, and only when the shot has no clean
frame at all *and* the background is genuinely out of focus. Then it can pass
as haze. It failed on a street scene with rooftops and power lines, and worked
on an out-of-focus grove.

Combining 1 and 3 is normal and is what `patch` emits: patch everything static,
delogo the small remainder. **Order matters** - the delogo must run *after* the
patches, so it interpolates from pixels already repaired instead of from the
artifact's own white. Running it first produced a bright rectangle.

## A speech bubble cannot be cropped out, so it needs patching

A bottom subtitle can be zoomed off the frame. A speech bubble cannot: it sits
in the upper middle over the actors' heads, so any crop that removes it removes
their faces too. On the third project two clips came back with a white bubble of
garbled Tamil about 200x180.

The worked example, for the one over a village street with roofs and poles:

```
split=4[kmMain][kmS1][kmS2][kmS3];
[kmS1]trim=start_frame=12:end_frame=13,setpts=PTS-STARTPTS,loop=loop=-1:size=1,
      setpts=N/24/TB,crop=246:164:712:98[kmP1];
[kmS2]...,crop=56:106:958:98[kmP2];
[kmS3]...,crop=26:30:956:204[kmP3];
[kmMain][kmP1]overlay=712:98:enable='between(t,1.30,4.10)'[kmO1];
[kmO1][kmP2]overlay=958:98:enable='...'[kmO2];
[kmO2][kmP3]overlay=956:204:enable='...'[kmO3];
[kmO3]delogo=x=952:y=232:w=34:h=26:enable='...'
```

Three patch rectangles rather than one, because a man in the crowd drifts about
25px through the lower right of the box across the window. A single rectangle
covering him froze his turban and cut a hard-edged bite out of his head, which
is worse than the smear was. The staircase of rectangles stops short of him and
the 34x26 delogo handles the sliver that is left.

The second clip's bubble sat over an out-of-focus grove and had no clean frame
in its shot at all, since the bubble is up at t=0. That one is a feathered blur,
and there it genuinely reads as sunlight haze:

```
delogo=x=506:y=30:w=224:h=194:enable='between(t,0.00,3.20)',split[kmA][kmB];
[kmB]gblur=sigma=48[kmL];
color=black:s=1280x720,format=gray,
geq=lum='255*exp(-(pow((X-618)/195\,2)+pow((Y-124)/175\,2)))'[kmM];
[kmL][kmM]alphamerge[kmP];[kmA][kmP]overlay=0:0:enable='between(t,0.00,3.20)'
```

Wide and soft beats tight and strong. Sigma 30 with a 158x142 ellipse streaked;
sigma 48 with a 195x175 ellipse did not. If the mask's alpha is still below
about 0.8 at the delogo box edge, delogo's own boundary shows as a seam.

Two label rules for any graph-shaped repair. Prefix every internal label
(`kmA`, not `a`), because `build_segment` wraps a repair in `[0:v]...[b]` when
it composites the watermark and a bare `[b]` inside the repair collides with it.
And commas inside `geq` must be escaped `\,` while commas inside `enable='...'`
are protected by the quotes.

## A graph-shaped repair has to be walked, not flattened

`kmlib.map_box` follows the provenance box through a repair so the watermark
lands on the mark. A patch repair contains `crop=246:164:712:98`, which read as
a flat chain says the mark was cropped away, so the watermark is placed as if
there were nothing to cover. The crop only ever applied to a small patch pasted
on top.

So `_split_graph` walks the graph: it tracks which pad carries the picture and
only applies filters on that path, and for a multi-input filter it requires the
main pad to be the *first* input, since that is the one whose geometry survives.
Branch filters are ignored. Any line reading `mark removed by the repair` on a
scene whose repair does not crop is this bug coming back.

## Check a repair with numbers, not just with your eyes

`qc.py verifyfix` renders each repaired scene, finds the area the repair
actually changed, and reports two things: how many bright pixels are left where
the artifact was, and the ratio of high-frequency energy inside the repaired
area against a clean frame of the same shot. A rebuild scores near 1. A blur
scores far below and fails.

`run.py verify` refuses to pass if any repair fails, which is the whole point:
the blur that shipped passed a full decode, passed a frame check, and looked
acceptable in a small crop. A number would have caught it.

Two details the check itself got wrong first:

- **The sharpness control is the local background, not an absolute.** A blur
  over out-of-focus grove scores 1.22 and passes, because that background
  really is that soft. The same technique over a street with rooftops fails.
  That is the right behaviour and it is why the gate does not need a per-project
  exception list.
- **A temporal repair cannot be compared frame to frame.** `trim`/`tpad` shifts
  the stream relative to the source, so an index-to-index diff reports the
  whole clip as changed and the artifact tests are meaningless anyway. That
  branch inspects the repaired stream alone, and decodes it with the same `-t`
  the builder uses, or tpad's hold comes out short and the check disagrees with
  the render it is checking.

## A clip whose tail is unusable: hold the last frame, do not trim

The best take of the film's payoff shot had a modern construction crane in its
last 2.5 seconds. The crane is unrepairable, the shot is unmissable, and
dropping it costs the ending. So hold the last clean frame over the bad tail:

```
split[kmMain][kmS];
[kmS]trim=start_frame=175:end_frame=176,setpts=PTS-STARTPTS,
     loop=loop=-1:size=1,setpts=N/24/TB[kmP];
[kmMain][kmP]overlay=0:0:enable='gte(t,7.30)'
```

Every frame survives and only the picture changes, which is what keeps the
audio, the segment length and every caption timing scaffolded off the clip
duration correct. On an emotional beat a two second hold reads as a deliberate
edit. Say in the report that it is there, and that regenerating the scene is
the real fix.

**Do not use trim plus tpad for this.** The obvious form,

```
trim=0:7.35,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=2.66
```

does not extend anything: it yielded 177 frames of a 240 frame clip. The
segment still probed as 10.01s because `apad` had padded the audio and `-t`
set the container length, so the file looked correct in every check that reads
metadata, while the last 2.6 seconds of that scene had no picture at all. It
shipped that way and only `qc.py verifyfix` caught it. Count decoded frames, not
container duration, whenever a repair touches time.

## An off-spec clip corrupts the film without failing

Flow occasionally returns a clip at a lower resolution than the rest of the
batch. Two on the third project came back 640x360 and 4 seconds instead of
1280x720 and 10.

Left alone, that breaks two things and only one of them is loud. The reel build
fails outright, because `crop=670:720` is not a valid crop on a 360px frame. The
film build *succeeds*: the concat demuxer stream-copies the small segment in,
the finished file still probes as 1280x720, and those seconds are broken picture
inside an otherwise clean render that passes a full decode with no errors.

So every segment is now put on the spec first, by `kmlib.spec_prefix`, before
any repair and before any reframe. That ordering is the point: a repair box, the
provenance box and the reel crop are all arithmetic on a 1280x720 frame, and
they only mean the same thing on every clip if every clip is that size by the
time they run. `inventory.py` still warns about mixed specs, and the warning is
worth reading, but the build no longer depends on anyone acting on it.

## Two reads of one clip differ by a level unless you pin the format

Every check in `qc.py` that scores a repair reads the clip twice, once raw and
once through the fix, and asks where they differ. That only means anything if
they are otherwise identical, and by default they are not. ffmpeg picks a
conversion path per graph: a bare `scale` can emit bgr24 in one step, while the
same `scale` feeding a filter that wants planar yuv goes the long way round.
The two reads then disagree by about one level **everywhere**.

One level is invisible. It is also well above the 6 that marks a repaired
pixel, so the check selects the entire frame as repaired, scores that against
the ring of picture just outside it - the frame edge - and prints a healthy
looking ratio. On a real repair that had ghosted the picture it said

    scene 82  ok
        repair touched x32-1134 y37-692 over 98 of 98 frames
        bright pixels there: 0 before, 0 after
        sharpness 31.3 against 30.4  ->  ratio 1.03

Nothing there is true. `kmlib.iter_frames` now pins `format=yuv420p` ahead of
the fix, which is what the decoder hands the builders anyway, and the same
repair scored 0.52 and failed. `verifyfix` also measures the frame-wide floor
now and says so out loud when it is not zero, because the next way this breaks
will not be the same way.

## An off-spec clip measures as noise that still looks like a picture

`kmlib.iter_frames` reads raw frames at a fixed size. Handed a 640x360 clip it
used to reshape the bytes into a 1280x720 buffer, which produces a skewed
smear of the real picture - and every box measured off it is in coordinates
that do not exist. It now puts the source on spec first, exactly as the
builders do. Two clips in the third project arrived at 640x360, so this is not
hypothetical.

## Scoring a reframe: judge it against the zoom, not against 1

A crop-and-rescale interpolates nothing. Every pixel that survives is real, and
the artifact is gone because it fell outside the new frame rather than because
something was painted over it, so neither the artifact test nor the
ring-sharpness test has anything to say about it.

What can go wrong is throwing away so much frame that the upscale turns to
mush, so that is what `verifyfix` measures - against `1/zoom**2`, which is
roughly what a clean upscale costs in high-frequency energy. Measured on the
third project: 1.18x scored 0.61, 1.25x scored 0.54. Judged against the 0.55
that a paint-over repair has to clear, a perfectly good 1.25x reframe fails,
and failing it pushes you back onto delogo - the technique the reframe exists
to avoid. Pass `--box` with the artifact bounds and the check also proves the
artifact is outside the new frame.

## Score a reframe against the picture it kept, not the whole source frame

**What happened.** The fourth project's three burned-in subtitles all got the
same 1.18x reframe. `verifyfix` passed one and failed the other two, at 0.29,
0.42 and 0.43 against a 0.43 gate. Three identical crops on three clips from
one batch cannot really differ that much, and they did not.

**The cause.** The control was the whole source frame, including the strip the
crop throws away. That strip contains the burned-in caption, and a caption is
the sharpest thing in the frame by a wide margin: crisp white glyphs with hard
edges. So the control counted the artifact's own edges as detail the reframe
had destroyed. Scene 6 measured 134.7 across the frame against 67.7 over the
part that actually survived. The control was double what it should have been,
and the better the crop was at removing text, the worse it scored.

**The fix.** `qc.kept_rect()` walks the chain in source units and returns the
rectangle the crop keeps; the control is that region of the source. Nothing
else changes, and the same three repairs then score 0.58, 0.58 and 0.57, within
a hundredth of each other, which is what one uniform crop should look like. It
tracks through a crop after a scale, and returns None for a `pad`, which adds
area that came from nowhere and would only flatter the repair.

Worth generalising: **any control that includes the thing being removed is
measuring the wrong quantity.** The failure is quiet in both directions here.
It failed good repairs on this project, and on a clip where the discarded strip
happens to be flat it would pass a bad one.

## A reframe is scored against the picture it kept, not the whole frame

**What happened.** All three burned-in captions on the fourth project were
reframed with the same `crop=1088:612:40:0,scale=1280:720`, and `verifyfix`
called two of them failed repairs: scene 6 scored 0.29 against a 0.43 gate,
scene 13 scored 0.42. Nothing was wrong with either. The check was comparing
the repaired frame against the **whole** source frame, and the strip the crop
throws away is the strip with the caption in it. A caption is the sharpest
thing in the frame by a wide margin, so its own glyph edges were counted as
detail the reframe destroyed. Scene 6 measured 134.7 across the frame against
67.7 over the part that actually survived: the control was double what it
should have been.

**The rule.** The control for a reframe is the region of the source the crop
kept, because the upscale is the only thing that happens to that region, and
mush from too tight a crop is the only thing this test is looking for.
`qc.kept_rect` tracks that rectangle in source units through the chain, so a
crop after a scale still lands in the right place, and it returns None for a
`pad`, which adds area that came from nowhere and would only flatter the
repair. Scored that way all three captions land at 0.57-0.58, within a
hundredth of each other, which is what one uniform crop applied three times
should look like.

**Why this matters more than the numbers.** A gate that fails good repairs gets
switched off. This one would have pushed the repair back onto a 924x90 delogo
over a moving actor, which is the exact smear the reframe exists to avoid.

## A repair you edited does not rebuild unless the builder knows

Both builders cached a segment against its source clip's mtime. Editing
`fixes.json` changed no clip, so nothing was stale, so nothing rebuilt: you fix
the smear, the render reports success, and the smear is still in the file. That
is the worst shape a bug can take here, because every check downstream is
looking at the old encode.

Each segment now carries a `.fix` sidecar holding the repair it was built with,
and a segment is stale when that string no longer matches. Keying on
`fixes.json`'s mtime instead was the first attempt and it is worse than it
sounds - it makes all 58 scenes stale on any edit and turns a one-scene change
into a ten minute rebuild.

## Burned-in subtitles on a moving actor: reframe, do not paint

Flow sometimes burns its own subtitles into the picture, bottom centre, which
in a close shot means they sit on the actor. Worked through on scene 82 of the
third project, in the order worth repeating:

- **Patch from a clean frame** is always the first thing to try and it was
  measured out here, not assumed: zero of 72 cells were static, worst deviation
  90 to 104 levels. She moves and the crowd behind her moves.
- **delogo** passed the gate at 0.58 once the boxes were tightened to hug the
  glyphs, and at 2x it still leaves faint pale streaks where the letter stems
  were. Interpolating a 150px wide block across a lit surface leaves that
  every time. Tightening the box helps a lot, which is worth knowing: measure
  the glyph bounds and add 3 or 4 pixels, do not eyeball a generous box. Never
  split one text run into adjacent boxes, because the interior edges are text
  and delogo will interpolate from its own white.
- **Reframe** is the answer, and the trick is to crop hard enough to take the
  generator's provenance mark with it. Cropping only enough to lose the text
  clips that mark and pushes it into the very corner, where a round logo
  centred on it would hang off frame and `cover_position` correctly refuses:
  `EXPOSED, the mark moves to (1223, 677, 1278, 719)`. Crop above the mark
  instead and `map_box` returns `gone`, the logo stays at the position it holds
  in every other scene, and the builder says `mark removed by the repair`.

## -ss before -i restarts the clock at zero

Spot-checking a windowed repair with `ffmpeg -ss 2.5 -i clip.mp4 -vf "<fix>"`
shows the artifact still there and the repair apparently doing nothing. The
input seek makes the output timestamps start at 0, so `enable='between(t,1.3,4)'`
is false for the frame you asked for. The filter is fine; the test is wrong.

Check a window either by selecting a frame number in the graph
(`select='eq(n\,60)'`), or by putting `-ss` after `-i` so the seek is a decode
seek and `t` keeps its real value.

## Finding burned-in text: count how long a pixel stays bright, not how bright

**What failed.** Two per-frame detectors were tried and both are useless. A
morphological text-region detector missed two of three known defects. A
flat-bright-blob detector fired constantly on white clothing and rain. A
connected-component count in the lower band scored a clip with no text at all
(rain over water) higher than a clip that genuinely had a subtitle in it.

**What works.** Burned-in text is *static*. Rain, water and highlights move. So
accumulate, per pixel of the lower third, the fraction of frames where it is
bright and desaturated, keep the pixels above about 0.15, and save that as an
image. Text appears as readable words; everything else appears as vague blobs.
On a 23 clip project this identified exactly the two affected clips in one pass,
and reading the output takes seconds because the caption is literally legible in
it.

Use it to *find* defects, then `qc.py cuts` / `profile` / `measure` to place the
repair. Do not skip the 1.5s sweep either: the persistence map only looks in the
lower band, and headers appear in corners.

## A single frame per clip is not a QC pass

The first sweep checked one frame per clip and only the top-left corner. It
found the two persistent corner headers and missed all three transient
artifacts, one of which shipped in the long form and two reels. The shortest was
1.8 seconds out of 10. Sample every 1.5 seconds, across the whole frame.

## Why the artifacts exist at all

The prompts carry a DIALOGUE block, and Veo sometimes renders the words instead
of only speaking them. The template's NEGATIVE line only guards against wrong
faces and wrong styles. `missing_prompts.py` appends text negatives for exactly
this reason. Dropping the DIALOGUE block also prevents it but loses the
generated speech.

## Concat only stream-copies if every segment matches

The concat demuxer needs identical codec parameters including GOP structure.
That is why `kmlib.VENC` pins `-g 48 -keyint_min 48 -sc_threshold 0` and a fixed
timescale, and why every stage encodes through it. Change those settings in one
place or segments stop concatenating.

## Apply repairs at the source encode, not to the finished film

Repairing the assembled film costs a second generation of compression for no
benefit. Both `build_film.py` and `build_reels.py` apply repairs in the same
encode that reads the source clip, and reels are built from source rather than
by re-cutting the film.

## Renumbering a part means re-encoding it

The part label is burned into the picture, so changing `publish_as` requires
rebuilding those reels, not renaming files. Delete the old outputs afterwards so
the wrong file cannot be uploaded.

## Cover the generator's mark, or do not claim to

**What happened.** The logo was placed at a fixed corner margin and looked like
it covered the generator's provenance sparkle. It did not. The sparkle sits at
x1136-1183, y576-623 in every Flow clip, a fixed 48x48 square. The logo box
started at x1140, y580, so the sparkle poked out four pixels on two sides, and
because **the logo is round, its bounding box corners are transparent**, the
whole sparkle sat in the empty corner of the logo's square and stayed visible.
It shipped in the first film and in the second, and the user found it.

**Two things make this worse than it sounds.** It varies shot to shot with the
background behind it, so it reads as sloppy rather than as a fixed mark. And a
repair that crops and rescales moves the sparkle: the first zoom repair pushed
it into the extreme frame corner, where a round logo *cannot* cover it at any
size, because a circle placed inside a corner never reaches that corner.

**The fix.** Measure the mark once, put it in `project.json` as
`provenance_box`, and let `build_film.py` centre the logo on it per scene,
following it through that scene's repair with `kmlib.map_box`. Where a repair
moves it somewhere unreachable, change the repair rather than the logo: a crop
that removes the bottom strip can usually also be shifted sideways to drop the
mark off the right edge in the same step, which is what
`crop=1112:626:12:0,scale=1280:720` does.

**How to measure it.** Not by brightness. Take the per-pixel temporal *minimum*
over the clip, subtract a heavy blur of that minimum, and the fixed overlay is
what stands out: it is the only thing that stays lit under everything that ever
passes beneath it. That measured identically across all 23 clips at every
threshold from 5 to 40, which is how you know it is an overlay and not content.

**Verify by looking.** The numeric version of this test cannot tell a static
rock from a static overlay, and it will flag most scenes. Crop the corner out of
the finished film at several scenes and look at it.

**And the warning has to actually reach someone.** `build_reels.py` collected
`reel_wm_position`'s notes into a list and then never printed it. So the
`EXPOSED, the mark moves to ...` message - the single thing that function exists
to produce - went to nobody, for every reel ever built. Nothing was wrong in the
reels it happened to build, because the 191% crop takes x305-975 and the mark
sits at x1136-1183, so it is cropped away and the note is empty. That is luck,
not a guarantee: a repair that shifts the mark inward, or a different reel
scale, puts it back in shot and the build would have stayed silent about it.
Fixed to print the notes the way `build_film.py` does. Worth checking any new
reporting path the same way: a warning that is computed and discarded looks
exactly like a warning that never fired.

The invisible SynthID watermark is unaffected by any of this, and so is any
platform-level synthetic content disclosure. This is about the logo covering the
visible mark cleanly instead of half covering it.

## "Unable to parse selected file" is usually the wrong file

YouTube rejected a caption upload, and the reflex is to assume the generator
wrote a bad `.srt`. Validate before believing that: parse every cue, check the
indices are sequential from 1, that each end is after its start, that none
overlap, that no cue is empty, and that nothing runs past the video. All 16
files were valid.

What actually invites the error is shipping `.docx` review copies in the same
folder as the subtitles, so one gets chosen in the file dialog. Hence
`captions/review/`. Single clear extensions (`_Tamil.srt`, not `.ta.srt`) help
for the same reason.

When a platform still refuses, hand over the `.sbv`. It is YouTube's own export
format, it carries no cue numbers to get out of step, and it is accepted when a
`.srt` is being argued with.

## Four script formats now, and neither of the last two carries prompts

`parse_script.py` reads all four, and which one arrived changes what the rest
of the pipeline has to do:

1. `KUMARAN: <tamil>` with SETTING/ACTION/CHARACTERS/NEGATIVE headings.
2. Tamil speaker `->` Tamil addressee, same headings.
3. A bare screenplay: `SCENE n`, parenthesised stage directions, a speaker name
   alone on a line, then the Tamil and its romanisation.
4. An arrow screenplay: `SCENE n`, a plain English sentence describing the shot,
   then Tamil speaker `->` addressee, the quoted Tamil, and the romanisation in
   brackets underneath. No headings anywhere.

The third is the one that needs care. It has no ACTION field, so the mapper has
nothing to score filenames against unless the parenthesised directions are
turned into one; `parse_screenplay` returns them separately for exactly that.
Tamil and romanisation are told apart by script rather than by line position,
because some lines are only one or the other. And a scene body runs to the next
`SCENE` heading, which sweeps up the following block's Tamil title, so the body
is cut at the first `BLOCK`/`Short #`/`Post order` line or that title is read as
the last speaker's dialogue.

It also has no prompts, which matters when scenes are unshot.
`missing_prompts.py` therefore synthesises one: the house style line, the
location, the direction lines as the action, and each speaking character's
description straight out of the character list. The description is the part that
earns its keep. Without it the generator invents a new person for a character
who is already on screen in fifty other clips.

The location needs forward-filling. A screenplay states where it is only when
the place changes, so scenes 10 to 14 inherit the stone under the banyan that
scene 9 named, and everything before the first stated place inherits the
script's own Location paragraph. Skip that and every dialogue-only scene gets a
prompt with no place in it at all.

The fourth format needs its own reader and its own detection, and both of those
were wrong on the first attempt in ways worth not repeating.

**Its action line is unparenthesised prose, so `parse_screenplay` cannot be
widened into it.** There a parenthesised line over six words is a stage
direction, and here every romanisation over six words is parenthesised too, so
the two rules contradict outright. `parse_arrow_body` reads it instead: an
arrow line opens a speech, a quoted Tamil line is the speech, a parenthesised
line closes it as the romanisation, and anything else is the shot description.
A voice-over is labelled with a bare Tamil name and no arrow (`கண்ணன் குரல்`),
so an unquoted Tamil line with nobody currently speaking is a speaker, not a
line.

**Which format arrived has to be decided once for the whole document, never per
scene.** Two separate failures come from deciding per scene. A scene with no
dialogue contains no arrow, so all seven silent scenes in the fourth project
were read as a bare screenplay and lost their action line, which in a
heading-less script is the only thing the clip mapper has to work with. And the
arrow notation is not exclusive to this format: format 2 writes its speakers
the same way but keeps its headings, so the presence of headings is what tells
the two apart. Without that check a format 2 scene body is read as dialogue in
full, prompt text included. `is_arrow_script` takes both into account.

**`"NO DIALOGUE"` contains `"DIALOGUE"`.** `parse_dialogue` searches for its
heading unanchored, so a scene whose body is one prose line and the words NO
DIALOGUE matched, and it returned the empty remainder after that line: no
dialogue, no error, and no action either. That is harmless in the heading
formats and wrong in this one, which is the second reason the format is settled
in `main` and this reader is called directly.

**An `END CARD` is not the last scene's dialogue.** The scene body cutoff
already handled `BLOCK` / `Short #` / `Post order`; a script ending on a FINAL
END CARD put its two Tamil lines into scene 40 as one more speaker and one more
line, and its English gloss into that scene's action.

## A cold open cut from the caption scaffold opens on half a word

The fourth project's part 9 was specified as "the last line of part 8 again as
a 1-2 second cold open". The caption scaffold put that line at 7.46-9.93 in a
10.01s clip, so a 2 second tail looks right.

It is not. The scaffold does not know where anyone speaks. `lay_out` spreads
each scene's lines across the clip weighted by Tamil character count, which is
a reasonable default for a subtitle and worthless as an edit point. Measured
off the audio, the line runs **6.88-8.45**. A 2 second tail starts at 8.01 and
opens the finale on the last two syllables.

So measure the audio. A 25ms RMS envelope over 16k mono, keep runs above about
12% of peak that last more than 200ms, merge anything less than 300ms apart,
and the utterances fall out cleanly; scene 38 gave six of them and the last
real line was obvious. Cut inside the silence in front of the line, not at the
line, which put the real cut at 6.70 and made the cold open 3.3s rather than
the 2s that was asked for. Say that in the report: the number in the brief was
derived from a length estimate, and the footage disagrees with it.

Two smaller things that follow from the same imprecision:

- **The trim shifts every later cue**, so `captions.py:reel_rows` has to read
  the same `cold_open` entry the builder reads. Otherwise the reel's whole
  track is late by whatever was trimmed.
- **A cue that ends just after the cut is a straggler, not a line.** Part 9's
  first caption was the line *before* its cold open, on screen for 0.7s,
  because the scaffold's end for it fell 0.68s past the cut. Cues with under
  0.8s left after a trim are dropped. That rule is scoped to the trimmed scene
  only: `lay_out` floors a genuine short line at 0.6s, so applying it
  everywhere would delete `ஆமா.` from every reel it appears in.

## The script's own cut list beats an assumed block size

Blocks are not always uniform. The second project runs 3, 3, 3, 3, 3, 3, 5,
because its three-shot ending belongs in one reel with the two scenes that set
it up. `parse_script.py` reads scene ranges out of the cut list
("Short #7  Scenes 19-23") and uses them when they cover every scene exactly
once, falling back to `--block-size` otherwise. It prints which it did. Read
that line: uneven blocks are correct far more often than they look wrong.

Script formats also drift between projects. Dialogue is written either as
`SPEAKER: text` or as `Tamil name -> addressee` on its own line above a quoted
line; `SETTING` is sometimes `ENVIRONMENT`. Both forms are parsed. If a new
script breaks the parser, widen it rather than rewriting the script, and check
the change against the older script before moving on.

## A filtergraph path is relative to the process, not to the project

`kmlib.rel()` strips the drive letter, so every ffmpeg call that uses a relative
watermark, font or overlay path must pass `cwd=root`. `build_segment` omitted it
and worked for a year purely because the project folder happened to be the
shell's working directory. It failed the moment a project was built from its
parent. If a render says "No such file or directory" for a path that plainly
exists, this is why.

## Environment

- ffmpeg is not on PATH. `imageio_ffmpeg` ships the build; there is no ffprobe,
  so durations come from parsing `ffmpeg -i` stderr.
- A 10 second 720p clip is about 660MB decoded. Stream frames
  (`kmlib.iter_frames`) rather than loading a clip into memory.
- The Windows console is cp1252 and cannot print Tamil. `kmlib` reconfigures
  stdout and stderr to UTF-8 on import, which covers every bundled script. A
  one-off `python -c` that does not import it will still die on
  `UnicodeEncodeError`, so write Tamil to a file and read the file instead.
