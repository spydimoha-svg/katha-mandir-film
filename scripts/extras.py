"""
Thumbnail candidates for the film and each reel.

Picks frames that tend to work as thumbnails: a face reasonably large in frame,
crisp rather than mid-motion, and not a near-duplicate of another candidate.
It scores rather than decides, and writes several options per target, because
what makes a good thumbnail is a judgement call this cannot make.

Scoring, in plain terms:
  skin      how much of the frame is skin-toned, as a stand-in for "a face is
            present and big" without needing a face model
  detail    edge energy, which drops on motion-blurred frames
  centre    weights the middle of frame, since thumbnails get cropped at the edges

Usage:
    python extras.py --root <project dir> thumbs [--per 3]
"""

import argparse
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib


def sampling(dur):
    """Step between candidates, and the minimum gap between chosen frames.

    Both scale with length. A fixed 2s gap on a 10 minute film returns three
    frames from the same shot, which is useless: thumbnail options should come
    from different moments in the story. A fixed fine step also makes a long
    film needlessly slow to scan.
    """
    step = 0.5 if dur <= 180 else max(1.0, dur / 600.0)
    return step, max(4.0, dur / 12.0)


def skin_fraction(bgr):
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    _, cr, cb = cv2.split(ycrcb)
    mask = ((cr > 135) & (cr < 180) & (cb > 85) & (cb < 135)).astype(np.uint8)
    return float(mask.mean()), mask


def score_frame(bgr):
    h, w = bgr.shape[:2]
    skin, mask = skin_fraction(bgr)
    detail = float(cv2.Laplacian(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY),
                                 cv2.CV_64F).var())
    cx0, cx1 = int(w * 0.2), int(w * 0.8)
    cy0, cy1 = int(h * 0.15), int(h * 0.85)
    centre = float(mask[cy0:cy1, cx0:cx1].mean())
    return (min(skin, 0.35) / 0.35) * 0.5 + min(detail / 900.0, 1.0) * 0.25 + \
           (min(centre, 0.4) / 0.4) * 0.25


def candidates(path):
    dur = kmlib.duration(path)
    step, gap = sampling(dur)
    out = []
    t = 0.3
    while t < dur - 0.2:
        f = kmlib.frame_at(path, t)
        if f is not None:
            out.append((score_frame(f), round(t, 2)))
        t += step
    return sorted(out, reverse=True), gap


def spread(picked, t, min_gap):
    return all(abs(t - p) >= min_gap for p in picked)


def cmd_thumbs(root, cfg, a):
    outdir = os.path.join(root, "thumbnails")
    os.makedirs(outdir, exist_ok=True)
    targets = [(cfg["film_out"], os.path.join(root, cfg["film_out"]))]
    for p in sorted(glob.glob(os.path.join(root, "reels", "*.mp4"))):
        targets.append((os.path.basename(p), p))

    for name, path in targets:
        if not os.path.exists(path):
            continue
        # basename, since film_out may carry directory separators
        stem = os.path.splitext(os.path.basename(name))[0]
        ranked, gap = candidates(path)
        picked = []
        for sc, t in ranked:
            if len(picked) >= a.per:
                break
            if spread(picked, t, gap):
                picked.append(t)
        for i, t in enumerate(picked, 1):
            out = os.path.join(outdir, "%s_thumb%d.jpg" % (stem, i))
            kmlib.run(["-ss", "%.2f" % t, "-i", path, "-frames:v", "1",
                       "-vf", "scale=1920:-2:flags=lanczos", "-q:v", "2", out])
        print("%-40s %s" % (stem[:38], ", ".join("%.1fs" % t for t in picked)))
    print("\nwrote %d files to %s" % (len(glob.glob(os.path.join(outdir, "*.jpg"))),
                                      os.path.relpath(outdir, root)))
    print("These are candidates ranked by face size and sharpness, not final art.")
    print("Pick one per video and add the title text yourself.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("thumbs")
    t.add_argument("--per", type=int, default=3)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    {"thumbs": cmd_thumbs}[a.cmd](root, cfg, a)


if __name__ == "__main__":
    main()
