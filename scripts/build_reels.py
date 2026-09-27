"""
Cut the film into vertical reels, one per script block.

The script is already a reels plan: fixed-size blocks whose last scene is the
hook. Using those as the boundaries means no scene is ever split, every reel
ends on a designed cliffhanger, and the reels together are exactly the long form
with nothing added or lost.

Vertical framing reproduces the CapCut setup used on earlier Katha Mandir reels:
a 9:16 canvas with the 16:9 source at 191% scale, centred. See kmlib.REEL for
the measurement that backs those numbers.

Reels are built from the source clips rather than by re-cutting the finished
film. The film's watermark sits outside the vertical crop, and going from source
keeps the reels at one generation of compression instead of two.

Captions are NOT burned in. They ship as sidecar .srt files (captions.py docs)
so a viewer can switch them off, the platform can index and auto-translate them,
and the wording can be corrected later without re-rendering a frame. Pass
--burn-captions only if a specific upload needs them baked into the picture.

The part label sits in the lower bar, tight under the picture. Grid previews on
Instagram and YouTube crop in from the top and bottom, so a label centred in the
upper bar is the first thing lost when the post is seen small.

Usage:
    python build_reels.py --root <project dir> [--reels 1,3] [--burn-captions]
                          [--no-label] [--force]
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib
import tamil_text

FONTS = {}                     # filled in main(): absolute and root-relative

WM_WIDTH = 92
WM_MARGIN = 32
LABEL_SIZE = 54
LABEL_GAP = 22         # below the picture edge, inside the lower bar
CAP_TAMIL_SIZE = 44
CAP_ENG_SIZE = 30


def geometry():
    """crop_w, crop_x, band_h, pad_y for the 191% look, forced to even pixels."""
    R = kmlib.REEL
    crop_w = int(round(R["src_w"] / R["scale"])) // 2 * 2
    crop_x = (R["src_w"] - crop_w) // 2
    band_h = int(round(R["src_h"] * R["w"] / crop_w)) // 2 * 2
    pad_y = (R["h"] - band_h) // 2
    return crop_w, crop_x, band_h, pad_y


CAP_TOP = 34                   # gap between the picture and the first caption row
CAP_MAX_W = kmlib.REEL["w"] - 2 * 48


def tamil_jobs(caps, scenes):
    """Render jobs for every Tamil caption line that will be drawn.

    Batched for the whole build because each call costs a process launch, and
    because the shaper is also the only thing that can measure the wrap.
    """
    jobs = []
    for s in scenes:
        for i, l in enumerate((caps.get(str(s)) or {}).get("lines", [])):
            t = (l.get("tamil") or "").strip()
            if t:
                jobs.append({"id": "cap_%d_%d" % (s, i), "text": t,
                             "size": CAP_TAMIL_SIZE, "color": kmlib.BRAND["cream"]
                             .replace("0x", "#"), "max_width": CAP_MAX_W,
                             "max_lines": 2, "min_size": 30})
    return jobs


def caption_overlays(lines, tam, scene, pad_y, band_h, first_input):
    """(filter steps, image files, drawtext steps) for one scene's captions.

    Tamil is overlaid as pre-shaped PNGs rather than drawn with drawtext, which
    silently loses the -u vowel signs; see tamil_text.py. English stays as
    drawtext since Latin needs no shaping.
    """
    b = kmlib.BRAND
    bar_top = pad_y + band_h
    steps, imgs, texts = [], [], []
    n = first_input
    for i, l in enumerate(lines):
        eng = (l.get("english") or "").strip()
        r = tam.get("cap_%d_%d" % (scene, i))
        window = "enable='between(t,%.3f,%.3f)'" % (l["start"], l["end"])
        y = bar_top + CAP_TOP
        if r:
            lh = tamil_text.line_height(r["size"])
            pad = int(round(r["size"] * 0.45))     # padding baked into the PNG
            for j, ln in enumerate(r["lines"]):
                imgs.append(ln["file"])
                steps.append(("%d:v" % n, "overlay=x=(W-w)/2:y=%d:%s"
                              % (y + j * lh - pad, window)))
                n += 1
            y += len(r["lines"]) * lh + 10
        if eng:
            en_lines, en_size = kmlib.fit_text(eng, FONTS["latin"], CAP_ENG_SIZE,
                                               CAP_MAX_W, min_size=20)
            for text in en_lines:
                texts.append("drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=%s:"
                             "x=(w-tw)/2:y=%d:%s"
                             % (FONTS["latin_rel"], kmlib.dt_escape(text), en_size,
                                b["brass"], y, window))
                y += int(en_size * 1.35)
    return steps, imgs, texts


def reel_wm_position(cfg, fix, scene, crop_w, crop_x, band_h, pad_y):
    """(x, y) for the logo on a reel, plus a note.

    The 191% framing crops the sides away, and the generator's provenance mark
    lives near the right edge, so on this framing it is usually gone before the
    logo is placed. That is a property of the crop, not a guarantee: a different
    scale, or a repair that moves the mark inward, can bring it back into shot.
    So it is followed through the full chain every time rather than assumed.
    """
    R = kmlib.REEL
    default = (R["w"] - WM_WIDTH - WM_MARGIN,
               pad_y + band_h - WM_WIDTH - WM_MARGIN)   # on the picture, not the bar
    box = cfg.get("provenance_box", kmlib.PROVENANCE_BOX)
    if not box:
        return default, ""
    chain = ((fix + ",") if fix else "") + \
        "crop=%d:%d:%d:0,scale=%d:%d,pad=%d:%d:0:%d" % (
            crop_w, R["src_h"], crop_x, R["w"], band_h, R["w"], R["h"], pad_y)
    mapped = kmlib.map_box(box, chain)
    if mapped == "gone":
        return default, ""
    if mapped is None:
        return default, ("scene %d: reel geometry could not be followed, logo left "
                         "in the default corner" % scene)
    pos = kmlib.cover_position(mapped, WM_WIDTH, (R["w"], R["h"]))
    if pos is None:
        return default, ("scene %d: EXPOSED in the reel, the mark lands at %s where "
                         "a %dpx round logo cannot cover it"
                         % (scene, tuple(int(v) for v in mapped), WM_WIDTH))
    return pos, "scene %d: mark visible in the reel, logo moved onto it" % scene


def build_scene(src, dst, fix, wm, label, lines, tam, scene, root, cfg, notes,
                start=0.0):
    crop_w, crop_x, band_h, pad_y = geometry()
    R = kmlib.REEL
    b = kmlib.BRAND
    d = kmlib.duration(src) - start

    # Spec, then repair, then reframe, and the order matters for both of the
    # first two: the crop below is arithmetic on a 1280x720 frame and a repair
    # box was measured on one, so an off-spec clip has to be scaled up before
    # either of them means anything.
    head = kmlib.join_chain(kmlib.spec_prefix(src), fix)
    parts = ["[0:v]%scrop=%d:%d:%d:0,scale=%d:%d:flags=lanczos,pad=%d:%d:0:%d:black[v0]"
             % (head + "," if head else "", crop_w, R["src_h"], crop_x,
                R["w"], band_h, R["w"], R["h"], pad_y)]
    (wx, wy), note = reel_wm_position(cfg, fix, scene, crop_w, crop_x, band_h, pad_y)
    if note:
        notes.append(note)
    parts.append("[v0][1:v]overlay=%d:%d[v1]" % (wx, wy))

    steps, imgs, texts = caption_overlays(lines, tam, scene, pad_y, band_h, 2)
    k = 1
    for label_in, filt in steps:
        parts.append("[v%d][%s]%s[v%d]" % (k, label_in, filt, k + 1))
        k += 1

    tail = []
    if label:
        # In the lower bar, tight under the picture rather than centred in it.
        # A grid preview crops in from the top and bottom, so every pixel of
        # distance from the picture edge is distance the label may not survive.
        tail.append("drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=%s:"
                    "x=(w-tw)/2:y=%d"
                    % (FONTS["latin_rel"], kmlib.dt_escape(label),
                       LABEL_SIZE, b["cream"], pad_y + band_h + LABEL_GAP))
    tail += texts
    parts.append("[v%d]%s[v]" % (k, ",".join(tail) if tail else "null"))

    # An input seek for a cold open, so the segment carries the tail of the clip
    # and nothing before it. This resets the clip's clock to zero, which is
    # exactly why main() refuses a cold open on a scene that carries a repair:
    # a windowed enable='between(t,..)' would then fire against the wrong
    # timestamps and the render would succeed with the artifact back in shot.
    args = (["-ss", "%.3f" % start] if start else []) + ["-i", src, "-i", wm]
    for p in imgs:
        args += ["-i", kmlib.rel(p, root)]
    af = "afade=t=in:st=0:d=0.04,afade=t=out:st=%.3f:d=0.04,apad" % (d - 0.04)
    kmlib.run(args + ["-filter_complex", ";".join(parts),
                      "-map", "[v]", "-map", "0:a", "-af", af, "-t", "%.3f" % d]
              + kmlib.VENC + kmlib.AENC + [dst], cwd=root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--reels", help="comma separated block numbers")
    ap.add_argument("--no-label", action="store_true")
    ap.add_argument("--burn-captions", action="store_true",
                    help="burn captions into the picture. Off by default: they "
                         "ship as sidecar .srt files from captions.py docs, so a "
                         "viewer can switch them off and the wording can be fixed "
                         "later without re-rendering.")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    work = cfg["work"]
    script = kmlib.load_json(os.path.join(root, "script.json"))
    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    have = {m["scene"]: m["file"] for m in edl["map"]}
    fixes = {}
    fpath = os.path.join(root, "fixes.json")
    if os.path.exists(fpath):
        fixes = {int(k): v for k, v in kmlib.load_json(fpath).items()}
    # A segment is stale when the repair changed, not only when the clip did.
    # Keyed on the source alone, editing a repair rebuilt nothing and every reel
    # kept the old one, which is the worst kind of failure here: you fix the
    # smear, the render reports success, and the smear is still in the file.
    #
    # The repair is fingerprinted per scene in a sidecar rather than compared
    # against fixes.json's mtime, because that mtime makes every scene stale on
    # any edit and turns a one-scene change into a full rebuild. An absent
    # sidecar reads as "no repair", so scenes that never had one stay cached.
    def fix_state(dst, scene):
        want = fixes.get(scene) or ""
        side = dst + ".fix"
        try:
            got = open(side, encoding="utf-8").read()
        except OSError:
            got = ""
        return want, got, side

    def stamp(side, want):
        if want:
            open(side, "w", encoding="utf-8").write(want)
        elif os.path.exists(side):
            os.remove(side)
    caps = {}
    cpath = os.path.join(root, "captions.json")
    if os.path.exists(cpath) and a.burn_captions:
        caps = kmlib.load_json(cpath)["scenes"]

    fonts = kmlib.prepare_fonts(os.path.join(work, "assets"))
    FONTS.update(fonts)
    FONTS["latin_rel"] = kmlib.rel(fonts["latin"], root)
    wmpath = os.path.join(work, "assets", "watermark_round_%d.png" % WM_WIDTH)
    if not os.path.exists(wmpath):
        subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "make_watermark.py"),
                        "--width", str(WM_WIDTH),
                        "--out", os.path.join(work, "assets")], check=True)
    wm = kmlib.rel(wmpath, root)

    # Published part number, where it differs from the block number. Set this in
    # project.json when a block can never be made, so the published run has no
    # gap. Clear it if the missing scenes ever get generated.
    publish_as = {int(k): int(v) for k, v in (cfg.get("publish_as") or {}).items()}
    blocks = {b["n"]: b for b in script["blocks"]}
    want = [int(x) for x in a.reels.split(",")] if a.reels else sorted(blocks)

    # Every Tamil caption for this run, shaped in one pass.
    wanted_scenes = [s for bn in want if bn in blocks
                     for s in blocks[bn]["scenes"] if s in have]
    tam = tamil_text.render_batch(tamil_jobs(caps, wanted_scenes),
                                  os.path.join(work, "captext")) if caps else {}

    outdir = os.path.join(root, "reels")
    os.makedirs(outdir, exist_ok=True)
    built, skipped, notes = [], [], []
    for bn in want:
        blk = blocks.get(bn)
        if not blk:
            continue
        scenes = [s for s in blk["scenes"] if s in have]
        if not scenes:
            skipped.append((bn, "no footage"))
            continue
        pub = publish_as.get(bn, bn)
        label = None if a.no_label else "PART %d" % pub
        segdir = os.path.join(work, "reels", "r%02d" % bn)
        os.makedirs(segdir, exist_ok=True)

        # A cold open trims the block's first scene to its tail. Both conditions
        # are checked here rather than in kmlib so the error can name the block:
        # a trim anywhere but the first scene would cut into the middle of the
        # reel, and a trim on a repaired scene shifts the timestamps its
        # windowed repair fires on, which fails by putting the artifact back
        # while the render still reports success.
        co = kmlib.cold_open(cfg, bn)
        if co and co[0] != scenes[0]:
            raise SystemExit("cold_open for reel %d names scene %d, but that "
                             "reel starts on scene %d" % (bn, co[0], scenes[0]))
        if co and fixes.get(co[0]):
            raise SystemExit("cold_open for reel %d is on scene %d, which carries "
                             "a repair. The seek would shift the repair's window; "
                             "pick another scene or repair it unwindowed." % (bn, co[0]))

        parts = []
        for i, s in enumerate(scenes, 1):
            src = os.path.join(root, cfg["clip_dir"], have[s])
            dst = os.path.join(segdir, "%02d_s%02d.mp4" % (i, s))
            start = co[1] if (co and s == co[0]) else 0.0
            want, got, side = fix_state(dst, s)
            # The cold open goes in the same sidecar as the repair, so changing
            # `from` in project.json makes the segment stale. Left out, editing
            # it rebuilt nothing and the reel kept the old opening.
            if start:
                want = "%s|cold_open=%.3f" % (want, start)
            if (a.force or not os.path.exists(dst)
                    or os.path.getmtime(dst) < os.path.getmtime(src)
                    or want != got):
                lines = caps.get(str(s), {}).get("lines", [])
                build_scene(src, dst, fixes.get(s), wm, label, lines, tam, s,
                            root, cfg, notes, start=start)
                stamp(side, want)
            parts.append(dst)

        # A Tamil block title makes a Tamil filename, which survives on disk but
        # is awkward in an upload queue, so project.json can name the file in
        # Latin. The burned-in label and the publish copy still use the Tamil.
        name_src = (cfg.get("reel_titles") or {}).get(str(bn)) or blk["title"]
        safe = "".join(ch if ch.isalnum() else "_" for ch in name_src).strip("_")
        name = "REEL_%02d_%s.mp4" % (pub, safe)
        out = os.path.join(outdir, name)
        listfile = os.path.join(segdir, "concat.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for p in parts:
                fh.write("file '%s'\n" % p.replace("\\", "/"))
        kmlib.run(["-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy",
                   "-movflags", "+faststart", out])
        d = kmlib.duration(out)
        built.append((pub, name, len(scenes), len(blk["scenes"]), d))
        print("%-36s %d/%d scenes  %5.1fs" % (name, len(scenes), len(blk["scenes"]), d))

    print("\n%d reels in %s" % (len(built), os.path.relpath(outdir, root)))
    # reel_wm_position's warnings were collected and then dropped on the floor,
    # so an EXPOSED mark in a reel - the one thing that function exists to catch
    # - was reported to nobody. build_film.py prints its notes; this now does
    # too. Deduplicated because a note is raised per scene, not per reel.
    for n in dict.fromkeys(notes):
        print("  " + n)
    if skipped:
        print("not built: " + ", ".join("#%d (%s)" % x for x in skipped))
    short = [b for b in built if b[2] < b[3]]
    if short:
        print("incomplete (scenes missing inside the block): "
              + ", ".join("part %d (%d/%d)" % (b[0], b[2], b[3]) for b in short))
    print("covered: %.1fs of footage" % sum(b[4] for b in built))


if __name__ == "__main__":
    main()
