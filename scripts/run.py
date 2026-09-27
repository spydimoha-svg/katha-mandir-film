"""
Phase driver. Runs the mechanical stages in order and stops where judgment is
needed, rather than making you retype eight commands and remember the sequence.

The pipeline has exactly three places where a person or the model has to decide
something, and each one is a hard stop here:

    prep     parse the script, inventory the clips        -> you write edl.json
    qc       sweep and scan for burned-in text,           -> you write fixes.json
             scaffold captions                               and the English
    deliver  build film, reels, captions, thumbs, verify

A phase refuses to run if the file the previous phase was waiting for is not
there. That is the point: the failure mode this prevents is a full render
against a clip map nobody confirmed, which looks fine until the story is
scrambled and every output has to be rebuilt.

Usage:
    python run.py --root <project dir> prep
    python run.py --root <project dir> qc
    python run.py --root <project dir> deliver [--force]
    python run.py --root <project dir> verify
"""

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kmlib


def step(script, root, *args):
    print("\n" + "=" * 72)
    print("$ %s %s" % (script, " ".join(str(a) for a in args)))
    print("=" * 72)
    r = subprocess.run([sys.executable, os.path.join(HERE, script),
                        "--root", root] + [str(a) for a in args])
    if r.returncode != 0:
        raise SystemExit("%s failed; stopping here rather than building on it"
                         % script)


def need(root, name, why):
    if not os.path.exists(os.path.join(root, name)):
        raise SystemExit("%s is missing.\n%s" % (name, why))


def cmd_prep(root, a):
    step("parse_script.py", root, *(["--block-size", a.block_size]
                                    if a.block_size else []))
    step("inventory.py", root)
    print("""
STOP. Two things before `qc`:

  1. project.json: fill in title_tamil, title_tamil_line2, title_latin,
     film_out, and reel_titles (a Latin filename per block). Tamil goes in
     LOGICAL order, the way it is typed; the renderer shapes it.

  2. edl.json: the scene-to-clip map. The proposed mapping above is a starting
     point and was 60% correct on the reference project, so read every contact
     sheet in .tmp/km/ and confirm each row against the scene's ACTION line.
     Rows marked CHECK are where the two methods disagree and were right only
     37% of the time.

Then show the user the mapping and get it confirmed. This is the one checkpoint
that is expensive to skip.""")


def cmd_qc(root, a):
    need(root, "edl.json",
         "Write the confirmed scene-to-clip map first. Running QC against clips\n"
         "you have not mapped tells you nothing about which scene to repair.")
    step("qc.py", root, "sweep")
    step("qc.py", root, "scan")
    step("captions.py", root, "scaffold")
    print("""
STOP. Two things before `deliver`:

  1. Look at every sweep sheet and at the scan grid in .tmp/km/. Real text is
     legible in the scan grid; ignore its pixel counts. For each defect run
     qc.py cuts / profile / measure and collect fixes.json. Prefer a zoom-crop
     over delogo for anything sitting on an actor.

  2. captions.json: write the english field for each line. Keep it short.

Nothing here is optional-but-fine to skip: an unrepaired burned-in caption ships
into the film and all its reels at once.""")


def cmd_deliver(root, a):
    need(root, "edl.json", "Run `prep` and write the confirmed map first.")
    caps = os.path.join(root, "captions.json")
    if not os.path.exists(caps):
        print("note: no captions.json, building without caption files")
    force = ["--force"] if a.force else []
    step("build_film.py", root, *force)
    step("build_reels.py", root, *force)
    if os.path.exists(caps):
        step("captions.py", root, "docs")
    step("extras.py", root, "thumbs")
    cmd_verify(root, a)
    print("""
Still yours to do, and the run is not finished without them:

  - Look at the title card, at every repair, and at one caption bar at full
    size. A decode with no errors is not the same as a correct picture.
  - Write PUBLISH.md, and add the titles to the channel's title ledger.
  - Report what is wrong or uncertain with timecodes, not just what was built.""")


def cmd_verify(root, a):
    cfg = kmlib.load_project(root)
    # Repairs first. A decode with no errors says nothing about whether a
    # repair replaced an artifact with a smear, and a smear is what a viewer
    # notices, so it is checked before anything else is called verified.
    if os.path.exists(os.path.join(root, "fixes.json")):
        r = subprocess.run([sys.executable, os.path.join(HERE, "qc.py"),
                            "--root", root, "verifyfix"])
        if r.returncode != 0:
            raise SystemExit("A repair above is a blur, not a repair. Fix it with\n"
                             "`qc.py patch` and rebuild those scenes before you\n"
                             "call any of this delivered.")
    films = [os.path.join(root, cfg["film_out"])]
    reels = sorted(glob.glob(os.path.join(root, "reels", "*.mp4")))
    print("\n" + "=" * 72)
    print("verify: full decode, spec and audio")
    print("=" * 72)
    bad = 0
    for f in films + reels:
        if not os.path.exists(f):
            print("%-46s MISSING" % os.path.basename(f)[:44])
            bad += 1
            continue
        q = subprocess.run([kmlib.FF, "-v", "error", "-i", f, "-f", "null", "-"],
                           capture_output=True, text=True)
        p = kmlib.probe(f)
        # Decoded frames, not the container's duration. A repair that touches
        # time can leave a file that probes at full length off its padded audio
        # while the picture ran out seconds earlier, and every metadata-level
        # check passes it.
        frames = sum(1 for _ in kmlib.iter_frames(f, w=p["w"], h=p["h"]))
        vid = frames / (p["fps"] or 24.0)
        ok = (not q.stderr.strip() and p["has_audio"]
              and abs(vid - p["dur"]) < 0.5)
        bad += 0 if ok else 1
        print("%-46s %4dx%-4d %6.2fs video=%6.2fs audio=%-5s %s"
              % (os.path.basename(f)[:44], p["w"], p["h"], p["dur"], vid,
                 p["has_audio"], "ok" if ok else "PROBLEM"))
    caps = sorted(glob.glob(os.path.join(root, "captions", "*.srt")))
    if caps:
        print("\ncaption files: %d subtitle files, %d review documents"
              % (len(glob.glob(os.path.join(root, "captions", "*.s*"))),
                 len(glob.glob(os.path.join(root, "captions", "review", "*")))))
        bad += _check_srt(caps)
    print("\n%s" % ("all checks passed" if not bad else "%d problems above" % bad))


def _check_srt(paths):
    """Parse every .srt the way a strict player would.

    Worth doing before delivery because "unable to parse selected file" from a
    platform sends you debugging the generator, when the usual cause is a wrong
    file being picked. Knowing the files are valid turns that into a one line
    answer instead of an investigation.
    """
    import re
    bad = 0
    for p in paths:
        text = open(p, encoding="utf-8").read().replace("\r\n", "\n")
        blocks = [b for b in text.split("\n\n") if b.strip()]
        prev_end, issues = -1.0, []
        for n, b in enumerate(blocks, 1):
            lines = b.split("\n")
            if len(lines) < 3 or not lines[2].strip():
                issues.append("cue %d empty" % n)
                continue
            if lines[0].strip() != str(n):
                issues.append("index %s at position %d" % (lines[0].strip(), n))
            m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> "
                         r"(\d\d):(\d\d):(\d\d),(\d\d\d)$", lines[1])
            if not m:
                issues.append("cue %d bad timestamp" % n)
                continue
            g = [int(x) for x in m.groups()]
            s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
            e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
            if e <= s:
                issues.append("cue %d not positive" % n)
            if s < prev_end - 0.001:
                issues.append("cue %d overlaps previous" % n)
            prev_end = e
        if issues:
            bad += 1
            print("  %-44s %s" % (os.path.basename(p)[:42], "; ".join(issues[:3])))
    if not bad:
        print("  all %d .srt files parse strictly" % len(paths))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--block-size")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("phase", choices=("prep", "qc", "deliver", "verify"))
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    {"prep": cmd_prep, "qc": cmd_qc,
     "deliver": cmd_deliver, "verify": cmd_verify}[a.phase](root, a)


if __name__ == "__main__":
    main()
