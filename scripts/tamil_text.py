"""
Shaped Tamil text as transparent PNGs, for overlaying instead of drawtext.

Why this exists, because it is the single most expensive thing to rediscover:

ffmpeg's drawtext CANNOT render Tamil correctly on this machine. Its output is
byte identical with text_shaping on and off, so harfbuzz is not running at all.
Without shaping, the ligating vowel signs U+0BC1 and U+0BC2 (the -u and -uu that
sit under or beside almost every Tamil word) have no standalone glyph and simply
vanish: "irukku" comes out as "irakka", "yaarum" as "yaaram". Pillow is no help
either, it is built without raqm. Reordering the string by hand fixes the
pre-base signs and does nothing at all for these.

Words still look like Tamil after the marks disappear, and they still look like
Tamil at thumbnail size, so this ships unless someone reads the caption at full
size. Assume nothing here is optional.

GDI+ shapes through Uniscribe and gets it right, and it is on every Windows
machine, so the text is rasterised there and overlaid as an image. One
PowerShell launch handles a whole build's worth of lines, and the wrap and
shrink happen there too, since only the shaper can measure what it will draw.

Usage:
    import tamil_text
    out = tamil_text.render_batch([
        {"id": "cap_3_0", "text": "...", "size": 44, "color": "#F0E4D0",
         "max_width": 984, "max_lines": 2, "min_size": 30}], workdir)
    out["cap_3_0"] -> {"size": 40, "lines": [{"file": ..., "w": ..., "h": ...}]}
"""

import json
import os
import shutil
import subprocess

FONT = "Nirmala Text"        # same face kmlib extracts from Nirmala.ttc,
                             # so cards keep the look of the earlier films
_PS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_tamil.ps1")


def available():
    return os.name == "nt" and shutil.which("powershell") is not None


def render_batch(jobs, workdir):
    """Rasterise every job, returning {id: {"size": n, "lines": [...]}}.

    Jobs are cached by their content hash in the filename the caller supplies,
    so a rebuild that changes one caption re-renders one caption.
    """
    if not jobs:
        return {}
    if not available():
        raise RuntimeError(
            "PowerShell is required to render Tamil text correctly; drawtext "
            "silently drops the -u vowel signs. See tamil_text.py.")
    os.makedirs(workdir, exist_ok=True)
    prepared = []
    for j in jobs:
        d = dict(j)
        d.setdefault("font", FONT)
        d.setdefault("color", "#FFFFFF")
        d.setdefault("max_lines", 1)
        d.setdefault("min_size", d["size"])
        d["out_prefix"] = os.path.join(workdir, d["id"])
        prepared.append(d)

    jobfile = os.path.join(workdir, "_jobs.json")
    outfile = os.path.join(workdir, "_out.json")
    with open(jobfile, "w", encoding="utf-8") as fh:
        json.dump(prepared, fh, ensure_ascii=False)
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-File", _PS,
                        "-JobFile", jobfile, "-OutFile", outfile],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0 or not os.path.exists(outfile):
        raise RuntimeError("tamil render failed:\n%s\n%s" % (p.stdout[-2000:],
                                                             p.stderr[-2000:]))
    with open(outfile, encoding="utf-8") as fh:
        res = json.load(fh)
    return {r["id"]: r for r in res}


def line_height(size):
    """Baseline-to-baseline spacing for stacked Tamil lines.

    Wider than a Latin equivalent because the rendered PNGs carry padding for
    the marks above and below the base letters.
    """
    return int(round(size * 1.30))
