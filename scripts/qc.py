"""
Find and measure text that Veo burned into the picture.

Veo sometimes draws the prompt rather than only performing it: a scene header in
a corner, or a hallucinated speech bubble carrying garbled dialogue. On the
reference project 5 of 65 clips were affected and two of them nearly shipped,
because the first pass sampled one frame per clip and only checked one corner.

Two things make these hard to catch and hard to repair, and both are why this
script exists rather than doing it by hand:

  They are transient. One bubble was on screen for 1.8 seconds out of 10. Any
  sweep coarser than about 1.5 seconds can step right over one.

  A clip can contain several shots. A repair window that runs past an internal
  cut lands the patch on whatever the next shot holds, which on the reference
  project was an actor's face. So spans are always clipped to shot boundaries.

Subcommands:
    sweep      contact sheets across every clip, for the visual pass
    scan       persistence map; static text comes out legible
    cuts       internal shot-cut times for one clip
    profile    what lives in a box across a whole clip, before you repair it
    measure    per-frame artifact box -> a ready-to-use delogo filter string
    patch      rebuild the area from a clean frame of the same shot
    verifyfix  did the repair leave a smear where the artifact was

Typical order: sweep, eyeball the sheets, then for each defect run profile to
see whether the box is ever occupied by a subject, then `patch` first and
`measure` only if `patch` says there is no clean frame to rebuild from. Finish
with verifyfix, which is the check that a removed artifact was not simply
replaced by a blur.
"""

import argparse
import glob
import os
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib

FPS = 24.0


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

def white_blob(img, box, thr):
    """A caption fill: flat, bright, rectangular, not touching the frame edge."""
    x0, y0, x1, y1 = box
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    m = cv2.morphologyEx((g >= thr).astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                         np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 3000 or w < 80 or h < 45 or a / float(w * h) < 0.55:
            continue
        if best is None or a > best[4]:
            best = (x, y, w, h, a)
    return None if best is None else (best[0] + x0, best[1] + y0, best[2], best[3])


def bright_text(img, box, thr):
    """Light glyphs on a dark ground: bounding box of everything bright."""
    x0, y0, x1, y1 = box
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(g >= thr)
    if len(xs) < 100:
        return None
    return (int(xs.min()) + x0, int(ys.min()) + y0,
            int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1)


DETECTORS = {"white_blob": white_blob, "bright_text": bright_text}


def find_cuts(path, thr=26):
    out, prev, n = [], None, 0
    for i, f in enumerate(kmlib.iter_frames(path)):
        n = i + 1
        g = cv2.cvtColor(cv2.resize(f, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None and float(np.abs(g - prev).mean()) > thr:
            out.append(i)
        prev = g
    return out, n


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_sweep(cfg, root, a):
    """Contact sheets sampling every clip at a fixed interval.

    1.5s is the default because the shortest artifact seen so far lasted 1.8s;
    any window at least as long as the interval is guaranteed to land on a frame.
    """
    clipdir = os.path.join(root, cfg["clip_dir"])
    files = sorted(glob.glob(os.path.join(clipdir, "*.mp4")))
    W = os.path.join(cfg["work"], "sweep")
    shutil.rmtree(W, ignore_errors=True)
    os.makedirs(W)
    manifest, k = [], 0
    for f in files:
        tmp = os.path.join(W, "tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        kmlib.run(["-i", f, "-vf", "fps=1/%s,scale=320:180" % a.step,
                   "-start_number", "0", os.path.join(tmp, "%03d.png")])
        for i, p in enumerate(sorted(glob.glob(os.path.join(tmp, "*.png")))):
            os.rename(p, os.path.join(W, "%04d.png" % k))
            manifest.append({"idx": k, "file": os.path.basename(f),
                             "t": round(i * float(a.step), 2)})
            k += 1
        shutil.rmtree(tmp, ignore_errors=True)
    kmlib.save_json(manifest, os.path.join(cfg["work"], "sweep_manifest.json"))
    for old in glob.glob(os.path.join(cfg["work"], "sweep_*.jpg")):
        os.remove(old)
    kmlib.run(["-framerate", "1", "-start_number", "0", "-i", os.path.join(W, "%04d.png"),
               "-vf", "tile=4x4:padding=3:color=red", "-q:v", "3",
               os.path.join(cfg["work"], "sweep_%02d.jpg")])
    sheets = sorted(glob.glob(os.path.join(cfg["work"], "sweep_*.jpg")))
    print("%d frames from %d clips -> %d sheets in %s"
          % (k, len(files), len(sheets), os.path.relpath(cfg["work"], root)))
    print("16 frames per sheet, row-major. Sheet N covers indices (N-1)*16 .. N*16-1.")
    cur, line = None, []
    for m in manifest:
        if m["idx"] % 16 == 0:
            if line:
                print("  sheet %02d: %s" % (m["idx"] // 16, "  ".join(line)))
            line, cur = [], None
        if m["file"] != cur:
            cur = m["file"]
            line.append("%s@%s" % (cur[:26], m["t"]))
        else:
            line[-1] += ",%s" % m["t"]
    if line:
        print("  sheet %02d: %s" % (len(manifest) // 16, "  ".join(line)))


def cmd_cuts(cfg, root, a):
    path = os.path.join(root, cfg["clip_dir"], a.clip)
    cuts, n = find_cuts(path)
    print("%s: %d frames" % (a.clip, n))
    if not cuts:
        print("  single continuous shot")
        return
    bounds = [0] + cuts + [n]
    print("  cuts at frames %s" % cuts)
    for s, e in zip(bounds, bounds[1:]):
        print("    shot frames %3d-%-3d  t=%.3f-%.3f" % (s, e - 1, s / FPS, (e - 1) / FPS))


def cmd_profile(cfg, root, a):
    """What occupies a box over the whole clip.

    Worth running before every repair. If the box never contains a subject, the
    filter can run unwindowed, which removes any chance of missing a fade edge.
    If it does, the window has to be tight and inside one shot.
    """
    path = os.path.join(root, cfg["clip_dir"], a.clip)
    x0, y0, x1, y1 = [int(v) for v in a.box.split(",")]
    print("%s  box x%d-%d y%d-%d" % (a.clip, x0, x1, y0, y1))
    print("  frame  t      mean   p200   p235")
    rows = []
    for i, f in enumerate(kmlib.iter_frames(path)):
        g = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        rows.append((i, float(g.mean()), int((g >= 200).sum()), int((g >= 235).sum())))
    for i, m, p2, p3 in rows:
        if p2 > 0 or i % 24 == 0:
            print("  %5d  %5.2f  %5.1f  %5d  %5d" % (i, i / FPS, m, p2, p3))
    means = [r[1] for r in rows]
    print("\n  mean luma over the clip: min %.1f  max %.1f" % (min(means), max(means)))
    print("  If max is close to min apart from the artifact window, nothing else")
    print("  ever enters this box and the repair can run unwindowed.")


CELL = 16                     # granularity of the static-background test
STATIC = 4.0                  # blurred deviation below this counts as static


def _still(frame_no):
    """A filtergraph fragment that turns one source frame into a held still."""
    return ("trim=start_frame=%d:end_frame=%d,setpts=PTS-STARTPTS,"
            "loop=loop=-1:size=1,setpts=N/%d/TB" % (frame_no, frame_no + 1, int(FPS)))


def cmd_patch(cfg, root, a):
    """Plan a repair that rebuilds the area from a clean frame of the same shot.

    This is the first thing to try on any artifact that sits on structured
    background, and it is the only technique that leaves no trace at all: the
    replaced pixels are the real pixels, taken from a moment when the artifact
    was not there.

    It only works where the background does not move, so that is measured
    rather than assumed. The box is divided into cells, each cell is compared
    across the shot's clean frames, and a cell counts as patchable only if it
    barely changes. Cells that do change are where an actor moves through, and
    pasting a still over those is what produces a rectangular bite out of a
    face - which looks far worse than the artifact did.

    The output is a full fix chain: one overlay per column-run of patchable
    cells, and a delogo sized to whatever is left. The delogo runs last, so it
    interpolates from pixels the patches have already repaired instead of from
    the artifact's own white.
    """
    path = os.path.join(root, cfg["clip_dir"], a.clip)
    x0, y0, x1, y1 = [int(v) for v in a.box.split(",")]
    t0, t1 = [float(v) for v in a.window.split(",")]
    f0, f1 = int(t0 * FPS), int(t1 * FPS + 0.5)

    cuts, n = find_cuts(path)
    bounds = [0] + cuts + [n]
    shot = next(((s, e) for s, e in zip(bounds, bounds[1:]) if s <= f0 < e), (0, n))
    # Stay clear of the shot's own edges. The frames either side of a cut are
    # already dissolving into the next shot, and comparing against those makes
    # a perfectly static background look like it moves, which disqualifies the
    # cells that were the whole reason to patch.
    lo, hi = shot[0] + a.edge, shot[1] - a.edge
    clean = [i for i in range(lo, hi) if not (f0 - 2 <= i <= f1 + 2)]
    if not clean:
        raise SystemExit("no clean frame inside this shot; the artifact covers all\n"
                         "of it, so there is nothing to rebuild from. Use a blur\n"
                         "blend instead and read the gotchas entry first.")
    frames = {i: f for i, f in enumerate(kmlib.iter_frames(path)) if i in clean}
    donor = min(clean, key=lambda i: min(abs(i - f0), abs(i - f1)))

    m = a.margin
    bx0, by0 = max(0, x0 - m), max(0, y0 - m)
    bx1, by1 = min(1280, x1 + m), min(720, y1 + m)
    cols = (bx1 - bx0 + CELL - 1) // CELL
    rows = (by1 - by0 + CELL - 1) // CELL
    # Both frames are blurred before differencing, and that is the whole trick.
    # What disqualifies an area is a person moving through it, not the picture
    # being pixel-identical: drifting dust, a shimmer of leaves and compression
    # noise all read as large deviations while being completely invisible once
    # patched. Blurring collapses those to nothing and leaves a displaced head
    # or arm as large as it was.
    def soft(img):
        return cv2.GaussianBlur(img.astype(np.float32), (0, 0), a.blur)

    ref = soft(frames[donor])
    per_cell = np.zeros((len(clean), rows, cols))
    for k, i in enumerate(clean):
        d = np.abs(soft(frames[i]) - ref).mean(axis=2)
        for r in range(rows):
            for c in range(cols):
                cy, cx = by0 + r * CELL, bx0 + c * CELL
                per_cell[k, r, c] = d[cy:cy + CELL, cx:cx + CELL].mean()
    # The worst clean frame, not a percentile. The frames after the window are
    # the only evidence of how far anything drifted across it, and there are
    # few of them, so a percentile discards exactly the samples that matter and
    # declares a moving actor static. Cut-adjacent frames are already excluded
    # above, which is what a percentile was going to guard against.
    dev = per_cell.max(axis=0)
    ok = dev <= a.static
    after = [i for i in clean if i > f1]
    if len(after) < 3:
        print("  WARNING only %d clean frame(s) after the artifact window, so how"
              % len(after))
        print("  far things drifted across it is barely measured. Treat the")
        print("  result as a starting point and look at the render.")

    # How far down each column stays patchable. Grouping equal depths gives a
    # staircase, which is usually two or three rectangles for a real artifact.
    depth = [next((r for r in range(rows) if not ok[r, c]), rows) for c in range(cols)]
    print("%s  scene %s" % (a.clip, a.scene))
    print("  shot frames %d-%d, artifact frames %d-%d, donor frame %d (t=%.2f)"
          % (shot[0], shot[1] - 1, f0, f1, donor, donor / FPS))
    print("  patch area x%d-%d y%d-%d, cells %dx%d" % (bx0, bx1, by0, by1, cols, rows))
    print("  patchable depth per column (of %d): %s" % (rows, depth))

    # Largest rectangle first, repeatedly. Grouping columns by equal depth
    # instead produced a fistful of 16px slivers and then hit the rectangle
    # limit before it ever reached the big patchable middle, so the residual
    # delogo ended up covering almost the whole artifact - which is the blur
    # this tool exists to avoid.
    avail = list(depth)
    rects = []
    while len(rects) < a.max_rects:
        best = None
        for l in range(cols):
            lo_d = avail[l]
            for r in range(l, cols):
                lo_d = min(lo_d, avail[r])
                if lo_d == 0:
                    break
                area = (r - l + 1) * lo_d
                if best is None or area > best[0]:
                    best = (area, l, r, lo_d)
        if not best or best[0] < 2:
            break
        _, l, r, h = best
        rects.append((bx0 + l * CELL, by0, (r - l + 1) * CELL, h * CELL))
        for c in range(l, r + 1):
            avail[c] = 0        # one rectangle per column keeps the graph small

    covered = np.zeros((720, 1280), bool)
    for px, py, pw, ph in rects:
        covered[py:py + ph, px:px + pw] = True
    left = ~covered[y0:y1, x0:x1]
    if left.any():
        ys, xs = np.nonzero(left)
        rx0, ry0 = x0 + xs.min() - 4, y0 + ys.min() - 4
        rx1, ry1 = x0 + xs.max() + 5, y0 + ys.max() + 5
        dl = "x=%d:y=%d:w=%d:h=%d" % (rx0, ry0, rx1 - rx0, ry1 - ry0)
    else:
        dl = None

    en = "enable='between(t,%.2f,%.2f)'" % (t0, t1)
    parts = ["split=%d[kmMain]%s" % (len(rects) + 1,
                                     "".join("[kmS%d]" % i for i in range(len(rects))))]
    for i, (px, py, pw, ph) in enumerate(rects):
        parts.append("[kmS%d]%s,crop=%d:%d:%d:%d[kmP%d]"
                     % (i, _still(donor), pw, ph, px, py, i))
    cur = "kmMain"
    for i, (px, py, pw, ph) in enumerate(rects):
        nxt = "kmO%d" % i
        parts.append("[%s][kmP%d]overlay=%d:%d:%s[%s]" % (cur, i, px, py, en, nxt))
        cur = nxt
    if dl:
        parts.append("[%s]delogo=%s:%s" % (cur, dl, en))
    else:
        parts[-1] = parts[-1].rsplit("[", 1)[0]
    chain = ";".join(parts)

    print("\n  %d patch rect(s): %s" % (len(rects), rects))
    print("  residual delogo: %s" % (dl or "none needed"))
    print("\nfixes.json entry for scene %s:\n" % a.scene)
    print('    "%s": %s' % (a.scene, json_str(chain)))
    print("\nNow run `qc.py verifyfix --scene %s` and look at it at full size."
          % a.scene)


def json_str(s):
    import json
    return json.dumps(s)


# Filters that only move or resample the picture. A chain built from nothing
# but these interpolates no new pixels, so it is scored as a reframe rather
# than as an artifact repair.
GEOMETRY = {"crop", "scale", "pad", "format", "setsar", "setdar", "null"}


def kept_rect(chain, size=(1280, 720)):
    """The source-side rectangle a reframe keeps, or None if it cannot be read.

    A reframe has to be scored against the picture it kept, not against the
    whole source frame, and the difference is not academic. A burned-in caption
    is the sharpest thing in the frame by a wide margin, so when the crop
    throws that strip away the whole-frame control counts the caption's own
    edges as detail the reframe destroyed. Scene 6 measured 134.7 across the
    frame against 67.7 over the part that survived: the control was double what
    it should have been, the ratio came out 0.29 against a 0.43 gate, and a
    reframe that interpolates nothing was reported as a failed repair. Scored
    against the kept region all three captions on this film land at 0.57-0.58,
    within a hundredth of each other, which is what a uniform crop should look
    like.

    Tracked in source units through the chain so a crop after a scale still
    lands in the right place. `pad` returns None: it adds area that came from
    nowhere, and averaging that in would only flatter the repair.
    """
    x, y, w, h = 0.0, 0.0, float(size[0]), float(size[1])
    cw, ch = float(size[0]), float(size[1])
    for step in kmlib._split_graph(chain):
        name, _, argstr = step.partition("=")
        name = name.strip()
        args = argstr.split(":") if argstr else []
        if name in ("format", "setsar", "setdar", "null", ""):
            continue
        if name == "crop" and len(args) >= 4:
            try:
                a_w, a_h, a_x, a_y = [float(v) for v in args[:4]]
            except ValueError:
                return None
            x += a_x * w / cw
            y += a_y * h / ch
            w, h = a_w * w / cw, a_h * h / ch
            cw, ch = a_w, a_h
        elif name == "scale" and len(args) >= 2:
            try:
                cw, ch = float(args[0]), float(args[1])
            except ValueError:
                return None
        else:
            return None
    return (int(round(x)), int(round(y)), int(round(w)), int(round(h)))


def cmd_verifyfix(cfg, root, a):
    """Check a repair removed the artifact without replacing it with a smear.

    Both halves matter and only the first is obvious. A blur blend does remove
    the artifact, and it passes any test that only asks whether the artifact is
    gone, while leaving a soft grey column that a viewer notices immediately.

    So sharpness is measured too. High-frequency energy inside the repaired
    area is compared against the same area in a clean frame of the same shot
    where one exists, and against the ring of picture just outside it where one
    does not. A repair that rebuilt real pixels scores near 1. A blur scores far
    below, and that is a failed repair however clean the artifact removal was.

    Both streams are walked in step rather than collected. A 10s 720p clip is
    660MB decoded and this compares two of them, so holding either one is how
    the check runs the machine out of memory instead of reporting anything.
    """
    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    fixes = kmlib.load_json(os.path.join(root, "fixes.json"))
    scenes = ([int(x) for x in a.scene.split(",")] if a.scene
              else sorted(int(k) for k in fixes))
    clip_of = {m["scene"]: m["file"] for m in edl["map"]}
    bad = 0
    for s in scenes:
        fix = fixes.get(str(s))
        if not fix:
            print("scene %d: no fix" % s)
            continue
        src = os.path.join(root, cfg["clip_dir"], clip_of[s])
        names = {f.split("=")[0].strip() for f in kmlib._split_graph(fix)}

        # A reframe is checked differently from everything else, and it has to
        # be caught before the comparison below, which assumes the two streams
        # line up pixel for pixel outside the repair. Under a crop nothing
        # lines up, so that comparison reports the whole frame as repaired and
        # then scores it against the frame edge.
        if names and names <= GEOMETRY and names & {"crop", "scale"}:
            # Nothing here is interpolated: every pixel that survives is a real
            # pixel, and the artifact is gone because it fell outside the new
            # frame rather than because something was painted over it. So the
            # artifact test and the ring-sharpness test both have nothing to
            # say. What can go wrong instead is throwing away so much of the
            # frame that the upscale turns the picture to mush.
            #
            # A clean upscale by z loses high-frequency energy roughly as
            # 1/z**2, so the ratio is judged against that and not against 1.
            # Judged against 1 a perfectly good 1.25x reframe scores 0.54 and
            # fails, which would push you back onto delogo - the technique the
            # reframe exists to avoid.
            mb = kmlib.map_box((590, 310, 690, 410), fix)
            zoom = ((mb[2] - mb[0]) / 100.0) if isinstance(mb, tuple) else None
            keep = kept_rect(fix)
            rs, fs = [], []
            for i, (r, f) in enumerate(zip(kmlib.iter_frames(src),
                                           kmlib.iter_frames(src, vf=fix))):
                if i % 12:
                    continue
                # Control is the part of the source the crop kept. Anything it
                # discarded is not evidence about the upscale, and when the
                # discarded strip is the artifact it is evidence pointing the
                # wrong way; see kept_rect.
                ctrl_img = (r[keep[1]:keep[1] + keep[3], keep[0]:keep[0] + keep[2]]
                            if keep else r)
                for buf, acc in ((ctrl_img, rs), (f, fs)):
                    g = cv2.cvtColor(buf, cv2.COLOR_BGR2GRAY)
                    acc.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
            ratio = float(np.mean(fs)) / max(float(np.mean(rs)), 1e-6)
            need = (0.6 / (zoom * zoom)) if zoom else a.min_sharp
            gone = (kmlib.map_box([int(v) for v in a.box.split(",")], fix)
                    if a.box else None)
            ok = ratio >= need and (gone == "gone" if a.box else True)
            bad += not ok
            print("scene %-3d %s" % (s, "ok" if ok else "PROBLEM"))
            print("    reframe: %s zoom, sharpness %.1f against %.1f "
                  "->  ratio %.2f, needs %.2f at this zoom"
                  % ("%.2fx" % zoom if zoom else "unknown", np.mean(fs),
                     np.mean(rs), ratio, need))
            print("    control: %s"
                  % ("the %dx%d of source the crop kept, at (%d, %d)"
                     % (keep[2], keep[3], keep[0], keep[1]) if keep else
                     "the whole source frame, because this chain's geometry "
                     "could not be followed"))
            if a.box:
                print("    the artifact box is %s the new frame"
                      % ("outside" if gone == "gone" else "STILL INSIDE"))
            else:
                print("    Pass --box with the artifact bounds and this will")
                print("    also prove the artifact is outside the new frame.")
            if not ok:
                print("    Either the reframe is too tight to hold up, or the")
                print("    artifact is still in shot. Look at it at full size.")
            continue

        # pass 1: where and when the repair changed the picture
        hot = np.zeros((720, 1280), np.float32)
        touched, n = [], 0
        for i, (r, f) in enumerate(zip(kmlib.iter_frames(src),
                                       kmlib.iter_frames(src, vf=fix))):
            d = np.abs(r.astype(np.float32) - f.astype(np.float32)).mean(axis=2)
            np.maximum(hot, d, out=hot)
            if d.mean() > 0.05:
                touched.append(i)
            n = i + 1
        # Threshold above the frame's own noise floor rather than at a fixed
        # level. The floor should be zero: two reads of one clip that differ
        # only by a filter should differ only where the filter ran. When it is
        # not zero the two reads took different conversion paths, and a fixed
        # threshold then selects the whole picture as repaired and goes on to
        # score it against the frame edge - passing the repair without ever
        # measuring it. So the floor is measured, used, and said out loud.
        floor = float(np.median(hot))
        ys, xs = np.nonzero(hot > max(6.0, floor + 6.0))
        if floor > 0.5:
            print("scene %d: NOTE the two reads differ by %.2f across the whole"
                  % (s, floor))
            print("    frame, not just inside the repair. Threshold raised to"
                  " %.2f to compensate," % (floor + 6.0))
            print("    but the numbers below are weaker than they look; find out"
                  " why the reads")
            print("    disagree before trusting them.")
        if not len(ys):
            print("scene %d: the fix changed nothing at all" % s)
            bad += 1
            continue
        rx0, ry0, rx1, ry1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        mid = touched[len(touched) // 2] if touched else 0

        cuts, total = find_cuts(src)
        bounds = [0] + cuts + [total]
        shot = next(((p, q) for p, q in zip(bounds, bounds[1:]) if p <= mid < q),
                    (0, total))
        clean = [i for i in range(shot[0], shot[1]) if i not in set(touched)]
        donor = min(clean, key=lambda i: abs(i - mid)) if clean else None

        # pass 2: only the two or three frames the scoring needs
        want = {mid, donor} - {None}
        raw, rep = {}, {}
        for i, (r, f) in enumerate(zip(kmlib.iter_frames(src),
                                       kmlib.iter_frames(src, vf=fix))):
            if i in want:
                raw[i], rep[i] = r.copy(), f.copy()
            # Deliberately no early break. Abandoning the pipes once the wanted
            # frames are in hand makes ffmpeg print a wall of broken-pipe errors
            # that look like the check itself failed, and reading to the end of a
            # ten second clip costs nothing worth having.
        # A temporal repair replaces an unusable stretch of time rather than a
        # box inside the picture, so neither artifact test means anything on it.
        # What matters there is that the hold is actually a hold.
        if names & {"trim", "tpad", "loop"} and not names & {"delogo", "overlay"}:
            # A temporal repair replaces a stretch of time, not a box in the
            # picture, so neither artifact test applies. It also shifts frames
            # relative to the source, which is why nothing here compares the
            # two streams index to index the way the artifact path does; that
            # comparison reported the whole clip as changed from t=1.12.
            #
            # Decoded with the same -t the builder uses, or tpad's hold is cut
            # short here and the check disagrees with the render it is checking.
            dur = kmlib.duration(src)
            prev, last_change, total = None, 0, 0
            for i, f in enumerate(kmlib.iter_frames(src, vf=fix,
                                                    extra=["-t", "%.3f" % dur])):
                if prev is not None and float(np.abs(f.astype(np.float32)
                                                     - prev).mean()) > 0.5:
                    last_change = i
                prev = f.astype(np.float32)
                total = i + 1
            hold = (total - last_change) / FPS
            kept = last_change / FPS
            ok = abs(total / FPS - dur) < 0.15 and hold > 0.2
            bad += not ok
            print("scene %-3d %s" % (s, "ok" if ok else "PROBLEM"))
            print("    temporal repair: %.2fs of picture, then a %.2fs hold"
                  % (kept, hold))
            print("    total %.2fs against the source's %.2fs" % (total / FPS, dur))
            if not ok:
                print("    A temporal repair has to come out the same length as")
                print("    the source, or the audio and every caption timing")
                print("    scaffolded off the clip duration go out with it.")
            print("    Whether the held frame is the right frame is a visual")
            print("    call. Look at it.")
            continue

        cut = (slice(ry0, ry1), slice(rx0, rx1))
        white_before = int((raw[mid][cut].min(axis=2) >= 245).sum())
        white_after = int((rep[mid][cut].min(axis=2) >= 245).sum())
        sharp = _lapvar(rep[mid][cut])
        if donor is not None:
            ctrl = _lapvar(raw[donor][cut])
            ctrl_from = "the same area in a clean frame of this shot"
        else:
            ring = rep[mid][max(0, ry0 - 40):min(720, ry1 + 40),
                            max(0, rx0 - 40):min(1280, rx1 + 40)]
            ctrl = _lapvar(ring)
            ctrl_from = "the ring of picture just outside the repair"
        ratio = sharp / ctrl if ctrl else 0.0
        # Only the sharpness ratio decides pass or fail. The bright-pixel count
        # is reported because it is useful, but it cannot be a gate: a repair
        # that holds a clean frame over an unusable tail changes the whole
        # picture without removing any bright artifact, and failing that is a
        # false alarm that trains everyone to ignore the real one. Whether the
        # artifact is gone is what the sweep and a full-size look are for. The
        # smear is the failure mode a person will not catch, so that is the one
        # with a number attached.
        clean_gone = white_after <= max(20, white_before * 0.08)
        ok = ratio >= a.min_sharp
        # One repair per project may genuinely have to be a blur: a shot whose
        # artifact is present from its first frame has no clean frame to rebuild
        # from. Recording the reason in project.json keeps that case out of the
        # gate without turning the gate into noise, which is how the blur got
        # shipped in the first place.
        excused = cfg.get("accepted_blur", {}).get(str(s))
        if not ok and clean_gone and excused:
            print("scene %-3d accepted blur: %s" % (s, excused))
            ok = True
        else:
            bad += not ok
        print("scene %-3d %s" % (s, "ok" if ok else "PROBLEM"))
        print("    repair touched x%d-%d y%d-%d over %d of %d frames"
              % (rx0, rx1, ry0, ry1, len(touched), n))
        print("    bright pixels there: %d before, %d after"
              % (white_before, white_after))
        print("    sharpness %.1f against %.1f from %s  ->  ratio %.2f"
              % (sharp, ctrl, ctrl_from, ratio))
        if not ok and ratio < a.min_sharp:
            print("    A ratio this low means the area was blurred rather than")
            print("    rebuilt. Try `qc.py patch` before accepting it.")
    print()
    print("every repair passed" if not bad else "%d repair(s) to redo" % bad)
    if bad:
        raise SystemExit(1)


def _lapvar(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def cmd_measure(cfg, root, a):
    """Per-frame artifact box -> delogo spans that never cross a shot cut."""
    path = os.path.join(root, cfg["clip_dir"], a.clip)
    det = DETECTORS[a.kind]
    box = [int(v) for v in a.box.split(",")]
    hits, cuts, prev, n = {}, [], None, 0
    for i, f in enumerate(kmlib.iter_frames(path)):
        n = i + 1
        g = cv2.cvtColor(cv2.resize(f, (320, 180)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None and float(np.abs(g - prev).mean()) > 26:
            cuts.append(i)
        prev = g
        b = det(f, box, a.thr)
        if b:
            hits[i] = b
    if not hits:
        print("nothing detected in %s with kind=%s thr=%d box=%s"
              % (a.clip, a.kind, a.thr, a.box))
        print("Try a lower threshold or a wider search box; the artifact may fade in.")
        return
    ks = sorted(hits)
    print("%s: artifact in frames %d-%d of %d (t=%.3f-%.3f)"
          % (a.clip, ks[0], ks[-1], n, ks[0] / FPS, ks[-1] / FPS))
    print("shot cuts: %s" % (cuts or "none"))

    bounds = [0] + cuts + [n]
    parts = []
    for s, e in zip(bounds, bounds[1:]):
        sel = [k for k in ks if s <= k < e]
        if not sel:
            continue
        lo, hi = max(sel[0], s), min(sel[-1], e - 1)
        # Snap to the shot when the artifact fills most of it. Detectors miss
        # fade-in and fade-out frames, and inside a single shot an over-wide
        # window costs nothing while a short one leaves a visible flash.
        if (hi - lo) > 0.6 * (e - 1 - s):
            lo, hi = s, e - 1
        i = lo
        while i <= hi:
            j = min(i + a.span - 1, hi)
            grp = [hits[k] for k in ks if i <= k <= j] or [hits[ks[0]]]
            xs = min(g[0] for g in grp) - a.margin
            ys = min(g[1] for g in grp) - a.margin
            xe = max(g[0] + g[2] for g in grp) + a.margin
            ye = max(g[1] + g[3] for g in grp) + a.margin
            x, y = max(1, xs), max(1, ys)
            w, h = min(xe - x, 1279 - x), min(ye - y, 719 - y)
            t0, t1 = max(0.0, (i - 0.5) / FPS), (j + 0.5) / FPS
            # A still artifact yields the same box span after span. Merging them
            # keeps the filter string readable, which matters because a human
            # has to sanity-check these coordinates.
            if parts and parts[-1][:4] == (x, y, w, h) and abs(parts[-1][5] - t0) < 1e-6:
                parts[-1] = (x, y, w, h, parts[-1][4], t1)
            else:
                parts.append((x, y, w, h, t0, t1))
            i = j + 1
    strs = ["delogo=x=%d:y=%d:w=%d:h=%d:enable='between(t,%.3f,%.3f)'" % p for p in parts]
    single = len(parts) == 1 and not cuts
    print("\nfixes.json entry for scene %s:" % a.scene)
    if single:
        p = strs[0].split(":enable=")[0]
        print('    "%s": "%s"' % (a.scene, p))
        print("\n  (unwindowed: single shot, one stable box. Run `profile` on this box")
        print("   first to confirm no subject ever enters it.)")
    else:
        print('    "%s": "%s"' % (a.scene, ",".join(strs)))
    print("\nVerify by rendering the clip with the filter and sampling across the")
    print("window AND just past each shot cut, at full size, before you build.")


def cmd_scan(cfg, root, a):
    """Find burned-in text by how long a pixel stays lit, not by how bright it is.

    Text is static; rain, water and highlights are not. So for every pixel in the
    lower band, count the fraction of frames where it is bright and desaturated,
    keep what survives, and write that per clip as an image. Real text comes out
    legible in the map; everything else comes out as vague blobs.

    This exists because per-frame scoring does not work. A connected-component
    count over the same band ranked a clip with no text at all above a clip that
    genuinely had a subtitle, and two earlier detectors missed known defects
    outright. Persistence separates them because it uses the one property the
    artifact has and the picture does not.

    Reading the grid is the point. Do not trust the pixel counts it prints.
    """
    import cv2
    import numpy as np

    clipdir = os.path.join(root, cfg["clip_dir"])
    edl_path = os.path.join(root, "edl.json")
    if os.path.exists(edl_path):
        items = [(m["scene"], m["file"]) for m in
                 sorted(kmlib.load_json(edl_path)["map"], key=lambda m: m["scene"])]
    else:
        items = [(i, os.path.basename(p)) for i, p in
                 enumerate(sorted(glob.glob(os.path.join(clipdir, "*.mp4"))), 1)]

    out = os.path.join(cfg["work"], "persist")
    os.makedirs(out, exist_ok=True)
    tiles = []
    for scene, fname in items:
        acc, n = None, 0
        for f in kmlib.iter_frames(os.path.join(clipdir, fname)):
            band = f[a.band:, :, :]
            hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
            m = ((hsv[:, :, 2] > 185) & (hsv[:, :, 1] < 55)).astype(np.float32)
            acc = m if acc is None else acc + m
            n += 1
        if acc is None:
            continue
        hot = ((acc / n) > a.persist).astype(np.uint8) * 255
        cv2.imwrite(os.path.join(out, "s%02d.png" % scene), hot)
        t = cv2.cvtColor(cv2.resize(hot, (430, 54)), cv2.COLOR_GRAY2BGR)
        cv2.putText(t, "s%d" % scene, (2, 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 0, 255), 1)
        tiles.append(t)
        print("s%-3d lit pixels %-7d %s" % (scene, hot.sum() // 255, fname[:44]))

    if not tiles:
        raise SystemExit("no clips scanned")
    while len(tiles) % 3:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)]
    grid = os.path.join(out, "grid.png")
    cv2.imwrite(grid, np.vstack(rows))
    print("\ngrid: %s" % os.path.relpath(grid, root))
    print("Look for tiles containing readable words. A high pixel count on its")
    print("own means nothing: bright water scores higher than a caption does.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep");   s.add_argument("--step", default="1.5")
    n = sub.add_parser("scan")
    n.add_argument("--band", type=int, default=560, help="top of the search band")
    n.add_argument("--persist", type=float, default=0.15,
                   help="fraction of frames a pixel must stay lit")
    c = sub.add_parser("cuts");    c.add_argument("clip")
    p = sub.add_parser("profile"); p.add_argument("clip"); p.add_argument("--box", required=True)
    m = sub.add_parser("measure")
    m.add_argument("clip")
    m.add_argument("--scene", required=True)
    m.add_argument("--kind", choices=sorted(DETECTORS), default="white_blob")
    m.add_argument("--box", required=True, help="search area x0,y0,x1,y1")
    m.add_argument("--thr", type=int, default=238)
    m.add_argument("--span", type=int, default=12, help="frames per delogo span")
    m.add_argument("--margin", type=int, default=7)

    t = sub.add_parser("patch", help="rebuild the area from a clean frame")
    t.add_argument("clip")
    t.add_argument("--scene", required=True)
    t.add_argument("--box", required=True, help="artifact bounds x0,y0,x1,y1")
    t.add_argument("--window", required=True, help="seconds t0,t1")
    t.add_argument("--margin", type=int, default=12)
    t.add_argument("--max-rects", type=int, default=4)
    t.add_argument("--edge", type=int, default=4,
                   help="frames to ignore at each end of the shot")
    t.add_argument("--static", type=float, default=STATIC,
                   help="blurred deviation below which a cell is patchable")
    t.add_argument("--blur", type=float, default=6.0,
                   help="sigma used to ignore texture-level change")

    v = sub.add_parser("verifyfix", help="did the repair leave a smear")
    v.add_argument("--scene", help="comma separated; default is every fix")
    v.add_argument("--box")
    v.add_argument("--min-sharp", type=float, default=0.55,
                   help="lowest acceptable sharpness ratio against the control")

    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    {"sweep": cmd_sweep, "scan": cmd_scan, "cuts": cmd_cuts,
     "profile": cmd_profile, "measure": cmd_measure,
     "patch": cmd_patch, "verifyfix": cmd_verifyfix}[a.cmd](cfg, root, a)


if __name__ == "__main__":
    main()
