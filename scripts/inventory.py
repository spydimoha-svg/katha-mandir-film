"""
Inventory the clips and propose a first-pass clip-to-scene mapping.

Flow names its exports after the prompt, so filenames carry real signal about
which scene a clip belongs to. This scores each filename against every scene's
ACTION and SETTING text and offers ranked candidates.

Treat those candidates as a starting point, never as the answer. The names are
generic ("Man_pulling_mud_from_well" fits three different scenes), several clips
in a run are second takes of the same scene, and a wrong mapping is expensive:
it scrambles the story and you only notice after a full render. So this also
emits contact sheets, and the mapping is meant to be confirmed against them by
eye before anything is built.

Usage:
    python inventory.py --root <project dir>
"""

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib

# Flow names its exports by role ("Boy_striking_temple_bell") while the script
# names its people ("Kumaran hauls himself up the pillar"). Bridging the two is
# most of what makes the scoring useful, so the bridge is built from the
# script's own character lock rather than hardcoded to one story's cast.
# A filename says "Man_bowing_to_boy" where the script says "watchman" or
# "daughter", so each specific role also answers to its coarse one, and both
# answer to "child" or the plural where that applies.
COARSE = {"landlord": "man", "watchman": "man", "guard": "man", "priest": "man",
          "farmer": "man", "merchant": "man", "servant": "man",
          "grandfather": "man", "elder": "man", "father": "man", "son": "boy",
          "cowherd": "boy", "grandmother": "woman", "mother": "woman",
          "daughter": "girl", "boy": "child", "girl": "child",
          "teacher": "man", "villager": "villagers"}
PLURALS = {"boys": "boy", "girls": "girl", "kids": "child", "kid": "child",
           "men": "man", "women": "woman", "children": "child",
           "villagers": "villager", "crowd": "villager"}
STOP = set("the a an and or of at in on to with for from into is are was were "
           "his her its their he she they it as by up down out".split())


def build_aliases(cast):
    """{role word: [character names]} from script.json's cast.

    Falls back to nothing rather than to a wrong cast: a filename token that
    resolves to no name simply scores on its other words, which is honest. A
    stale hardcoded cast would instead award confident points to the wrong scene.
    """
    out = {}

    def add(word, key):
        if key not in out.setdefault(word, []):
            out[word].append(key)

    for name, role in (cast or {}).items():
        # The key has to be a token the scene text will actually produce, so it
        # comes from the same tokeniser: "RED-DHOTI BOY" -> "dhoti", not
        # "red-dhoti", which would never match anything.
        parts = tokens(name)
        if not parts:
            continue
        key = max(parts, key=len)
        add(role, key)
        for w in parts:                     # THATHA also names him
            if w != key:
                add(w, key)
        coarse = COARSE.get(role)
        while coarse:                       # watchman -> man, boy -> child
            add(coarse, key)
            coarse = COARSE.get(coarse)
    for plural, single in PLURALS.items():
        for key in out.get(single, []):
            add(plural, key)
    return out


def tokens(s):
    s = re.sub(r"\d{6,}", " ", s.lower())
    return [t for t in re.split(r"[^a-z]+", s) if len(t) > 2 and t not in STOP]


def score(fname, scene, aliases):
    ft = tokens(os.path.splitext(fname)[0])
    # CHARACTERS is included because filenames lead with who is on screen, and
    # a scene's cast separates otherwise near-identical rain-on-a-path scenes.
    st = set(tokens(scene["action"] + " " + scene["setting"] + " "
                    + scene["camera"] + " " + scene.get("characters", "")))
    hits = 0.0
    for t in ft:
        if t in st:
            hits += 1.0
        else:
            for alias in aliases.get(t, []):
                if alias in st:
                    hits += 0.7
                    break
    return hits / max(len(ft), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--sheet-cols", type=int, default=4)
    ap.add_argument("--sheet-rows", type=int, default=4)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    script = kmlib.load_json(os.path.join(root, "script.json"))
    scenes = {int(k): v for k, v in script["scenes"].items()}
    aliases = build_aliases(script.get("cast"))
    clipdir = os.path.join(root, cfg["clip_dir"])

    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(clipdir, "*.mp4")))
    if not files:
        raise SystemExit("no mp4 files in " + clipdir)

    nums = sorted(scenes)
    specs, rows = {}, []
    for i, f in enumerate(files):
        info = kmlib.probe(os.path.join(clipdir, f))
        specs.setdefault((info["w"], info["h"], round(info["fps"], 2),
                          info["has_audio"]), []).append(f)
        ranked = sorted(((score(f, s, aliases), n) for n, s in scenes.items()),
                        reverse=True)
        rows.append({"idx": i, "file": f, "dur": round(info["dur"], 3),
                     "w": info["w"], "h": info["h"], "fps": info["fps"],
                     "has_audio": info["has_audio"],
                     "candidates": [{"scene": n, "score": round(sc, 3)}
                                    for sc, n in ranked[:3]]})

    # Scoring each clip independently lets several clips pile onto one popular
    # scene. Solving it as an assignment problem instead - each clip to a
    # distinct scene, maximising total score - measured meaningfully better on
    # this project's verified mapping (60% vs 51% exact).
    #
    # The more useful output is where the two methods disagree. On the reference
    # project, clips where they agreed were 76% correct and clips where they
    # disagreed only 37%. So disagreement marks exactly the clips worth staring
    # at, which is what makes the visual check tractable rather than tedious.
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
        M = np.array([[score(r["file"], scenes[n], aliases) for n in nums]
                      for r in rows])
        ri, ci = linear_sum_assignment(-M)
        for i, j in zip(ri, ci):
            rows[i]["assigned"] = nums[j]
    except ImportError:
        for r in rows:
            r["assigned"] = r["candidates"][0]["scene"]

    # contact sheets: one representative frame per clip, in filename order
    fdir = os.path.join(cfg["work"], "mapframes")
    os.makedirs(fdir, exist_ok=True)
    for r in rows:
        out = os.path.join(fdir, "%03d.jpg" % r["idx"])
        if not os.path.exists(out):
            kmlib.run(["-ss", min(2.0, r["dur"] / 3), "-i", os.path.join(clipdir, r["file"]),
                       "-frames:v", "1", "-vf", "scale=400:-2", out])
    sheets = os.path.join(cfg["work"], "mapsheet_%02d.jpg")
    for old in glob.glob(os.path.join(cfg["work"], "mapsheet_*.jpg")):
        os.remove(old)
    kmlib.run(["-framerate", "1", "-start_number", "0", "-i", os.path.join(fdir, "%03d.jpg"),
               "-vf", "tile=%dx%d:padding=3:color=red" % (a.sheet_cols, a.sheet_rows),
               "-q:v", "3", sheets])

    kmlib.save_json({"clip_dir": cfg["clip_dir"], "count": len(rows), "clips": rows},
                    os.path.join(root, "clips.json"))

    per = a.sheet_cols * a.sheet_rows
    print("clips: %d" % len(rows))
    if len(specs) > 1:
        print("WARNING mixed specs, the render assumes one uniform format:")
        for k, v in specs.items():
            print("   %sx%s @%s audio=%s  x%d  e.g. %s" % (k[0], k[1], k[2], k[3], len(v), v[0]))
    else:
        k = list(specs)[0]
        print("spec: %dx%d @%s fps, audio=%s (uniform)" % (k[0], k[1], k[2], k[3]))
    durs = sorted({r["dur"] for r in rows})
    print("durations: %s" % (durs if len(durs) < 6 else "%s .. %s (%d distinct)"
                             % (durs[0], durs[-1], len(durs))))
    print("\ncontact sheets: %s  (%d per sheet, row-major, index order below)"
          % (os.path.relpath(cfg["work"], root), per))
    for s in range(0, len(rows), per):
        print("  sheet %02d: idx %d-%d" % (s // per + 1, s, min(s + per - 1, len(rows) - 1)))
    disagree = [r for r in rows if r["assigned"] != r["candidates"][0]["scene"]]
    print("\nfirst-pass mapping. These are candidates, not answers - confirm every")
    print("row against the contact sheets before building anything.")
    print("CHECK marks where ranking and assignment disagree; on the reference")
    print("project those were right only 37%% of the time, against 76%% elsewhere.")
    for r in rows:
        c = r["candidates"]
        mark = "CHECK" if r["assigned"] != c[0]["scene"] else "     "
        print("  %s %3d %-44s rank %s | assign s%d"
              % (mark, r["idx"], r["file"][:42],
                 ", ".join("s%d(%.2f)" % (x["scene"], x["score"]) for x in c),
                 r["assigned"]))
    print("\n%d of %d rows disagree and need the closest look." % (len(disagree), len(rows)))
    unused = sorted(set(nums) - {r["assigned"] for r in rows})
    if unused:
        print("scenes with no clip proposed: %s" % unused)


if __name__ == "__main__":
    main()
