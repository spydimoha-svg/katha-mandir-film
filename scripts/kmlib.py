"""
Shared helpers for the Katha Mandir film pipeline.

Everything more than one stage needs lives here: locating ffmpeg, probing clips,
the one encode profile every stage must share, Tamil text handling, and project
layout. Import it rather than re-deriving any of this. Several of these are
places where the obvious approach is quietly wrong, and the comments say why.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import imageio_ffmpeg

# The Windows console defaults to cp1252, so any script that prints a Tamil
# speaker name or title dies on a UnicodeEncodeError halfway through its report.
# Every stage imports this module, so fixing it once here covers all of them.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ffmpeg is not on PATH on this machine. imageio_ffmpeg ships a full build
# (libx264, aac, delogo, drawtext, freetype, harfbuzz). There is no ffprobe, so
# durations get read out of ffmpeg's stderr instead.
FF = imageio_ffmpeg.get_ffmpeg_exe()

# Every segment of a render must be encoded identically, including the GOP, or
# the concat demuxer cannot stream-copy them together.
VENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", "24", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
        "-video_track_timescale", "24000"]
AENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

# Katha Mandir brand. Fixed on purpose, this skill serves one channel.
BRAND = {
    "channel": "Katha Mandir",
    "series_line": "Kannan Series",
    "ground": "0x17100A",     # warm near-black for cards and reel bars
    "gold": "0xE8B45C",       # Tamil title
    "gold_dim": "0xC9A05A",   # secondary Tamil line
    "cream": "0xF0E4D0",      # romanised title, part label, Tamil captions
    "brass": "0xA8875E",      # series line, English caption
    "rule": "0xC08A3E",
}

# Reel framing reproduces the CapCut setup used on the existing reels: a 9:16
# canvas with the 16:9 source at 191% scale, centred. That is a horizontal centre
# crop of 1280/1.91 = 670px upscaled to canvas width, with black bars above and
# below. Measured against a 2160x3840 export: content band 2302px, bars 769px.
# Output is 1080x1920 rather than 4K because the source is 720p and the crop is
# 670px wide, so 4K would be a 3.2x upscale for no added detail.
REEL = {"w": 1080, "h": 1920, "scale": 1.91, "src_w": 1280, "src_h": 720}

# Flow stamps a fixed provenance sparkle into every clip it exports. Measured
# identically in all 23 clips of the second project at every detection threshold
# from 5 to 40, and matching what the first project showed: a 48x48 square at
# x1136-1183, y576-623 on a 1280x720 frame. The watermark is placed to cover
# this rather than to sit at a corner margin, because a round logo parked in the
# corner leaves the mark showing in its own transparent corner.
#
# Treat this as a default, not as gospel. Re-measure if Flow changes its export
# (kmlib-style temporal minimum, see gotchas) and override per project by
# setting "provenance_box" in project.json.
PROVENANCE_BOX = [1136, 576, 1183, 623]

TAMIL_TTC = r"C:\Windows\Fonts\Nirmala.ttc"
TAMIL_FACE = 3          # Nirmala Text; freetype only loads face 0 of a collection
LATIN_TTF = r"C:\Windows\Fonts\georgia.ttf"


# --------------------------------------------------------------------------
# process helpers
# --------------------------------------------------------------------------

def run(args, cwd=None):
    p = subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error"] + [str(a) for a in args],
                       capture_output=True, text=True, errors="replace", cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n%s\n%s"
                           % (" ".join(map(str, args)), p.stderr[-3000:]))
    return p


def probe(path):
    """Dict of dur / w / h / fps / has_audio, read out of ffmpeg's stderr."""
    e = subprocess.run([FF, "-hide_banner", "-i", path],
                       capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", e)
    if not m:
        raise RuntimeError("no duration for " + path)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = [l for l in e.split("\n") if "Video:" in l]
    res = re.search(r"(\d{3,4})x(\d{3,4})", v[0]) if v else None
    fps = re.search(r"([\d.]+) fps", v[0]) if v else None
    return {"dur": dur,
            "w": int(res.group(1)) if res else 0,
            "h": int(res.group(2)) if res else 0,
            "fps": float(fps.group(1)) if fps else 0.0,
            "has_audio": any("Audio:" in l for l in e.split("\n"))}


def duration(path):
    return probe(path)["dur"]


def frame_at(path, t):
    """One decoded BGR frame as a numpy array, or None."""
    import cv2
    import numpy as np
    o = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t,
                        "-i", path, "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True)
    if not o.stdout:
        return None
    return cv2.imdecode(np.frombuffer(o.stdout, np.uint8), cv2.IMREAD_COLOR)


def iter_frames(path, w=1280, h=720, vf=None, extra=None):
    """Stream decoded frames. A 10s 720p clip is 660MB decoded, so this never
    holds them all at once.

    `vf` runs a filtergraph first, which is how a repair gets inspected as the
    renderer will actually see it rather than as a spot check of one seeked
    frame. Seeking with -ss ahead of the input restarts the clock at zero, so a
    windowed repair never fires in that kind of check.

    A source that is not already (w, h) is put on that spec before `vf` runs,
    exactly as the builders do. Without this the raw reader below reshapes a
    640x360 clip into a 1280x720 buffer and every measurement taken from it is
    noise that still looks like a picture, so a repair gets placed against
    coordinates that do not exist.

    The format is then pinned to yuv420p, which is what the decoder hands the
    builders anyway, so it costs nothing and it buys the one property every
    comparison here depends on: two reads of the same clip that differ only by
    a filter differ only where that filter touched. Left unpinned, ffmpeg picks
    the conversion path per graph - a plain scale can emit bgr24 directly,
    while the same scale ahead of a filter that wants planar yuv goes the long
    way round - and the two reads then disagree by about one level across the
    whole frame. That is far below anything visible and far above the threshold
    a repair is measured against, so a check comparing them selects the entire
    picture as repaired and scores it against nothing.
    """
    import numpy as np
    size = w * h * 3
    vf = join_chain(spec_prefix(path, (w, h)), "format=yuv420p", vf)
    args = [FF, "-hide_banner", "-loglevel", "error", "-i", path]
    if vf:
        args += ["-filter_complex", "[0:v]%s[v]" % vf, "-map", "[v]"]
    args += list(extra or [])
    p = subprocess.Popen(args + ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         stdout=subprocess.PIPE)
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    p.stdout.close()
    p.wait()


# --------------------------------------------------------------------------
# Tamil text
# --------------------------------------------------------------------------

# ffmpeg's drawtext cannot shape Tamil correctly on this machine. It leaves
# pre-base vowel signs on the wrong side of their consonant, and emits a
# dotted-circle placeholder when it decomposes a split sign. Its output is
# byte-identical with text_shaping on or off, so it is not really shaping at all.
# Pillow is no help either: no raqm, so no complex-script shaping whatsoever.
#
# The fix is to reorder here and hand drawtext glyphs already in visual order,
# with text_shaping=0. Always pair tamil_visual() with that option. Using either
# one without the other produces garbage on screen, and it is garbage that looks
# plausible at thumbnail size, so it ships unless you check at full size.
_PRE_BASE = {"\u0BC6", "\u0BC7", "\u0BC8"}                 # e, ee, ai
_SPLIT = {"\u0BCA": ("\u0BC6", "\u0BBE"),                  # o  = e  + aa
          "\u0BCB": ("\u0BC7", "\u0BBE"),                  # oo = ee + aa
          "\u0BCC": ("\u0BC6", "\u0BD7")}                  # au = e  + au-length


def _is_consonant(ch):
    return "\u0B95" <= ch <= "\u0BB9"


def _already_visual(text):
    """True if every pre-base sign already sits to the left of a consonant.

    Reordering is not idempotent, so a string that passes through this function
    twice comes out wrong. That is an easy mistake to make once captions flow
    through more than one helper, and the damage is silent: the text still looks
    like Tamil, just misspelt. Detecting the visual form and returning it
    untouched makes a double call harmless.
    """
    signs = [i for i, ch in enumerate(text) if ch in _PRE_BASE]
    if not signs:
        return False                     # nothing to judge by; treat as logical
    visual = sum(1 for i in signs
                 if i + 1 < len(text) and _is_consonant(text[i + 1]))
    logical = sum(1 for i in signs
                  if i > 0 and _is_consonant(text[i - 1]))
    return visual > logical


def tamil_visual(text):
    """Reorder Tamil from logical to visual order, for use with text_shaping=0.

    A pre-base sign renders to the LEFT of its consonant, and a split sign
    renders as one glyph on each side, so both have to be moved off the
    consonant they follow in the encoded string.
    """
    if _already_visual(text):
        return text
    out = []
    for ch in text:
        if ch in _PRE_BASE or ch in _SPLIT:
            i = len(out) - 1
            while i >= 0 and not _is_consonant(out[i]):
                i -= 1
            if i < 0:
                out.append(ch)          # stray sign, leave it alone
                continue
            if ch in _SPLIT:
                pre, post = _SPLIT[ch]
                out.insert(i, pre)
                out.append(post)
            else:
                out.insert(i, ch)
        else:
            out.append(ch)
    return "".join(out)


def prepare_fonts(workdir):
    """Fonts copied to colon-free, space-free paths, because a Windows drive
    letter inside an ffmpeg filtergraph is an escaping minefield. The Tamil face
    is extracted out of the .ttc since freetype only loads face 0."""
    os.makedirs(workdir, exist_ok=True)
    latin = os.path.join(workdir, "latin.ttf")
    if not os.path.exists(latin):
        shutil.copyfile(LATIN_TTF, latin)
    tamil = os.path.join(workdir, "tamil.ttf")
    if not os.path.exists(tamil):
        from fontTools.ttLib import TTCollection
        TTCollection(TAMIL_TTC).fonts[TAMIL_FACE].save(tamil)
    return {"latin": latin, "tamil": tamil}


def fit_text(text, font_path, size, max_w, max_lines=2, min_size=26):
    """Wrap, then shrink, until the text fits the width. Returns (lines, size).

    drawtext has no auto-fit and silently draws past the frame edge, which on a
    vertical reel clips the first and last words of a long line. PIL measures the
    same glyph sequence drawtext will draw (visual order, no shaping), so its
    width is a good predictor.
    """
    from PIL import ImageFont
    words = text.split()
    if not words:
        return [], size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        width = lambda s: font.getbbox(s)[2] - font.getbbox(s)[0]
        lines, cur = [], words[0]
        for w in words[1:]:
            if width(cur + " " + w) <= max_w:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(width(l) <= max_w for l in lines):
            return lines, size
        size -= 2
    return lines[:max_lines], max(size, min_size)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def spec_prefix(src, size=(1280, 720)):
    """A scale onto the project spec, or "" when the clip is already on it.

    Flow sometimes hands back a clip at a lower resolution than the rest of a
    batch, and an off-spec clip breaks two things quietly rather than loudly.
    The concat demuxer stream-copies it into the film at its own size, so those
    seconds decode as broken picture while the file still probes as 1280x720;
    and the reel crop is arithmetic on 1280x720, so `crop=670:720` on a 640x360
    source is simply an invalid crop and the whole reel fails to build.

    Every segment therefore starts by being put on the spec. It runs before any
    repair, which is what makes a repair box, the provenance box and the reel
    maths all mean the same thing on every clip regardless of what came out of
    the generator.
    """
    p = probe(src)
    if (p["w"], p["h"]) == tuple(size):
        return ""
    return ("scale=%d:%d:flags=lanczos:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (size[0], size[1], size[0], size[1]))


def join_chain(*parts):
    """Comma-join the filter chain pieces that are actually present."""
    return ",".join(p for p in parts if p)


# Filters that change nothing about where a pixel sits. Anything outside this
# set and the crop/scale/pad handled below means the mapping is not known, and
# a watermark placed from a guess is worse than one placed from the default.
_NO_MOVE = {"delogo", "drawtext", "drawbox", "eq", "hqdn3d", "unsharp", "null",
            "format", "fade", "boxblur", "curves", "colorbalance", "gblur",
            # A feathered-patch repair is a small graph rather than a flat
            # chain, and none of its parts move the frame: every branch is the
            # size of the base and the overlay lands at 0:0. Without these a
            # working repair reports "geometry could not be followed" and the
            # watermark falls back to a position it did not need to fall back
            # to.
            "split", "overlay", "alphamerge", "geq", "color", "nullsrc",
            "trim", "setpts", "tpad", "select", "loop", "fps"}


def _scan(text, seps):
    """Split on separators that are not escaped and not inside single quotes.

    Splitting naively gets this wrong twice over, and both forms appear in real
    repairs: a geq expression escapes its own commas as "\\,", and an enable
    expression quotes them, as in enable='between(t,1.3,4.05)'. Either one turns
    a single filter into several unrecognised ones, and the caller reads that as
    geometry it cannot follow.
    """
    out, buf, quoted, esc = [], [], False, False
    for ch in text or "":
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
            buf.append(ch)
        elif ch == "'":
            quoted = not quoted
            buf.append(ch)
        elif ch in seps and not quoted:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


_LABELS = re.compile(r"\[([^\]]*)\]")


def _split_graph(chain):
    """The filters that act on the main video stream, in order.

    A repair is not always a flat chain any more. A patch repair is a graph: it
    splits the frame, crops a still out of one branch and overlays it back. Read
    flat, that graph looks like it contains `crop=246:164:712:98`, so a box
    outside that crop is reported "gone" and the watermark is placed as if the
    provenance mark had been cropped away, when in truth the crop only ever
    applied to a small patch pasted on top.

    So the graph is walked rather than flattened. Only segments that consume the
    stream carrying the picture count, and for a multi-input filter that means
    being its *first* input, since that is the one whose geometry survives.
    """
    steps, cur = [], None            # cur: label carrying the picture, None = input
    for seg in _scan(chain, ";"):
        if not seg.strip():
            continue
        head = re.match(r"\s*((?:\[[^\]]*\])*)", seg)
        ins = _LABELS.findall(head.group(1))
        body = seg[head.end():]
        tail = re.search(r"((?:\[[^\]]*\])*)\s*$", body)
        outs = _LABELS.findall(tail.group(1))
        body = body[:tail.start()]
        on_main = (cur is None and not ins) or (ins and ins[0] == cur)
        if not on_main:
            continue
        steps += [s.strip() for s in _scan(body, ",") if s.strip()]
        cur = outs[0] if outs else None
    return steps


def map_box(box, chain, size=(1280, 720)):
    """Follow a source-frame box through a filter chain's geometry.

    Returns the mapped box, the string "gone" if a crop removed it entirely, or
    None if the chain contains geometry this cannot follow. The three outcomes
    are different decisions for the caller, so they are kept distinct: "gone"
    means there is nothing left to cover, None means do not trust any position
    derived from this.

    Only crop, scale and pad move anything, and those are exactly the filters
    this pipeline generates, so a repair written by `qc.py` or by hand in the
    documented form is always followable.
    """
    x0, y0, x1, y1 = [float(v) for v in box]
    w, h = float(size[0]), float(size[1])
    for step in _split_graph(chain):
        name, _, argstr = step.partition("=")
        name = name.strip()
        args = argstr.split(":") if argstr else []
        if name in _NO_MOVE:
            continue
        if name == "crop" and len(args) >= 4:
            try:
                cw, ch, cx, cy = [float(a) for a in args[:4]]
            except ValueError:
                return None
            if x1 < cx or x0 > cx + cw or y1 < cy or y0 > cy + ch:
                return "gone"
            x0, x1, y0, y1 = x0 - cx, x1 - cx, y0 - cy, y1 - cy
            w, h = cw, ch
        elif name == "scale" and len(args) >= 2:
            try:
                sw, sh = float(args[0]), float(args[1])
            except ValueError:
                return None
            fx, fy = sw / w, sh / h
            x0, x1, y0, y1 = x0 * fx, x1 * fx, y0 * fy, y1 * fy
            w, h = sw, sh
        elif name == "pad" and len(args) >= 4:
            try:
                pw, ph, px, py = [float(a) for a in args[:4]]
            except ValueError:
                return None
            x0, x1, y0, y1 = x0 + px, x1 + px, y0 + py, y1 + py
            w, h = pw, ph
        else:
            return None
    # A crop can leave the box only partly visible; clip it to what survives.
    x0, x1 = max(x0, 0.0), min(x1, w - 1)
    y0, y1 = max(y0, 0.0), min(y1, h - 1)
    if x1 < x0 or y1 < y0:
        return "gone"
    return (x0, y0, x1, y1)


def cover_position(box, logo_w, frame, round_logo=True):
    """Top-left for a logo of width `logo_w` that covers `box`, or None.

    The logo is round, so it only covers what falls inside its inscribed circle;
    the corners of its bounding square are transparent. That is exactly why a
    corner-placed logo can sit right beside a provenance mark and still leave it
    showing. So the logo is centred on the box, then pushed back inside the
    frame, and the result is only returned if it genuinely covers all four
    corners of the box.
    """
    import math
    fw, fh = frame
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    x = min(max(cx - logo_w / 2.0, 0.0), fw - logo_w)
    y = min(max(cy - logo_w / 2.0, 0.0), fh - logo_w)
    lx, ly, r = x + logo_w / 2.0, y + logo_w / 2.0, logo_w / 2.0
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    reach = max(math.hypot(px - lx, py - ly) for px, py in corners)
    if round_logo and reach > r:
        return None
    if not round_logo and not (x <= box[0] and y <= box[1]
                               and x + logo_w >= box[2] and y + logo_w >= box[3]):
        return None
    return int(round(x)), int(round(y))


def rel(path, root):
    """Filtergraph-safe relative path: forward slashes, no drive colon."""
    return os.path.relpath(path, root).replace("\\", "/")


def dt_escape(s):
    """Escape literal text for a drawtext text= value."""
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\u2019").replace("%", "\\%"))


# --------------------------------------------------------------------------
# project layout
# --------------------------------------------------------------------------

def cold_open(cfg, block):
    """(scene, start_seconds) for a reel that opens on the tail of a scene.

    A reel is otherwise whole scenes only, which is what stops a cut landing
    mid-line. A cold open is the one deliberate exception: the last beat of the
    previous part replayed as a recap, so a viewer arriving at part N knows
    which question they are watching someone answer.

    Set it per block in project.json, as the point in the clip to start from:

        "cold_open": {"9": {"scene": 38, "from": 6.70}}

    The scene must be the block's first and must carry no repair. The seek
    shifts every timestamp in the clip, so a windowed enable='between(t,..)'
    inside a repair would fire at the wrong moment, and silently: the render
    succeeds and the artifact is back. Both are checked by the caller, which
    can name the offending block in its error.

    Measure `from` off the audio, never off the caption scaffold. Those timings
    are text lengths spread across the clip rather than speech, so they miss by
    seconds. On this film the scaffold put scene 38's closing line at 7.46 when
    it actually starts at 6.88, and a tail cut from the scaffold's number would
    have opened the finale on half a word.
    """
    spec = (cfg.get("cold_open") or {}).get(str(block))
    if not spec:
        return None
    return int(spec["scene"]), float(spec["from"])


def load_project(root):
    cfg_path = os.path.join(root, "project.json")
    if not os.path.exists(cfg_path):
        raise SystemExit("no project.json in %s - run scripts/parse_script.py first" % root)
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg["root"] = root
    cfg["work"] = os.path.join(root, ".tmp", "km")
    os.makedirs(cfg["work"], exist_ok=True)
    return cfg


def save_json(obj, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def load_json(path):
    return json.load(open(path, encoding="utf-8"))
