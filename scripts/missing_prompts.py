"""
Generation pack for scenes that have no clip yet.

Each scene block in the script is already a complete, self-contained Flow prompt.
One thing gets added: text negatives.

On the reference project, 5 of 65 clips came back with the prompt drawn into the
picture, because the DIALOGUE block sometimes gets rendered as on-screen text
rather than only spoken. The template's NEGATIVE line only guards against wrong
faces and wrong styles, so this appends caption and subtitle negatives to it. All
five were repairable in the edit, but stopping it at generation is far cheaper.

Removing the DIALOGUE block entirely also prevents it, but that loses the
generated speech. Keep the block and the negatives if you want Veo's audio.

Usage:
    python missing_prompts.py --root <project dir> [--raw-negatives]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib

TEXT_NEGATIVES = ("text, letters, words, subtitles, captions, speech bubbles, "
                  "speech balloons, dialogue boxes, on-screen titles, watermark, signage")


STYLE_DEFAULT = ("warm stylised 3D animation, South Indian village, soft "
                 "daylight, shallow depth of field, character designs exactly "
                 "as the attached reference sheet")

OFF_MODEL = ("photorealistic, live action, modern clothing, modern vehicles, "
             "cranes, machinery, tarmac roads, road markings, bare-chested "
             "adults, costume changing mid-shot, extra characters, shot changes")


def harden(block):
    out = []
    for line in block.split("\n"):
        if line.startswith("NEGATIVE:") and TEXT_NEGATIVES not in line:
            line = line.rstrip().rstrip(",") + ", " + TEXT_NEGATIVES
        out.append(line)
    return "\n".join(out)


def synthesise(sc, script, cfg):
    """A full Flow prompt for a script that does not carry one.

    A bare screenplay is scene headings, parenthesised directions and dialogue,
    and nothing else. Pasting that into Flow returns a clip that matches the
    words and nothing else - wrong village, wrong clothes, wrong faces - because
    the generator has been told what is said and never what is seen.

    So the fields are assembled from what the script does state: the location
    carried forward from the last scene that named one, the direction lines as
    the action, and each speaking character's own description out of the
    character list. That last part is what makes a newly generated scene cut
    together with the clips already on disk instead of introducing a stranger.
    """
    desc = script.get("cast_desc") or {}
    who = []
    for name in [x.strip() for x in (sc.get("characters") or "").split(",") if x.strip()]:
        d = desc.get(name) or next((v for k, v in desc.items()
                                    if name in k.split() or k.startswith(name)), "")
        who.append("%s - %s" % (name, d) if d else name)
    said = []
    for dl in sc["dialogue"]:
        if not dl["tamil"]:
            continue
        said.append("%s: %s" % (dl["speaker"], dl["tamil"]))
        if dl["roman"]:
            said.append("(%s)" % dl["roman"])
    out = ["STYLE: %s" % cfg.get("style", STYLE_DEFAULT)]
    if sc.get("location"):
        out.append("SETTING: %s" % sc["location"])
    out.append("ACTION: %s" % (sc["action"] or "Hold on the speakers as they "
                               "talk. One continuous shot, no cuts."))
    if who:
        out.append("CHARACTERS: " + "; ".join(who))
    if said:
        out += ["DIALOGUE:"] + said
    out.append("NEGATIVE: %s, %s" % (OFF_MODEL, TEXT_NEGATIVES))
    out.append("Duration 10 seconds. One continuous shot, no shot changes.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--raw-negatives", action="store_true")
    ap.add_argument("--out", default="MISSING-SCENES-PROMPTS.md")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    script = kmlib.load_json(os.path.join(root, "script.json"))
    edl = kmlib.load_json(os.path.join(root, "edl.json"))

    have = {m["scene"] for m in edl["map"]}
    want = sorted(int(n) for n in script["scenes"] if int(n) not in have)
    if not want:
        print("every scene in the script has a clip; nothing to generate")
        return

    block_of = {}
    for b in script["blocks"]:
        for s in b["scenes"]:
            block_of[s] = b

    note = ("The NEGATIVE line in each block below has text negatives appended."
            if not a.raw_negatives else
            "NEGATIVE lines are exactly as in the script (--raw-negatives).")
    L = ["# Scenes still to generate", "",
         "%d scenes have no clip. Generate them in Flow, drop the files into" % len(want),
         "`%s/`, add their rows to edl.json, then rerun build_film.py and" % cfg["clip_dir"],
         "build_reels.py.", "",
         "Every block below is complete: paste it straight into Flow. Feed the",
         "approved character sheet in as a style reference each time.", "",
         "**Why the NEGATIVE lines differ from the script.** Veo sometimes draws the",
         "DIALOGUE block into the picture instead of only speaking it, as a corner",
         "header or a speech bubble full of garbled text. " + note, "",
         "Check every new clip for this before assembling. It is transient, often",
         "under two seconds, so look at several moments rather than one.", "",
         "| Scene | Reel | Beat |", "| --- | --- | --- |"]
    for n in want:
        blk = block_of.get(n)
        first = script["scenes"][str(n)]["action"][:95]
        L.append("| %d | #%s | %s |" % (n, blk["n"] if blk else "?", first))
    L.append("")
    for n in want:
        blk = block_of.get(n)
        sc = script["scenes"][str(n)]
        # A script that carries its own prompt fields is already a prompt; one
        # that does not has to have one built, or the block below is dialogue
        # with no picture in it.
        if "NEGATIVE:" in sc["prompt"] or "ACTION:" in sc["prompt"]:
            body = sc["prompt"] if a.raw_negatives else harden(sc["prompt"])
        else:
            body = synthesise(sc, script, cfg)
        L += ["---", "", "## SCENE %d  (Reel #%s%s)"
              % (n, blk["n"] if blk else "?",
                 ", " + blk["title"] if blk and blk.get("title") else ""), "",
              "```", body, "```", ""]

    out = os.path.join(root, a.out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("wrote %s (%d scenes)" % (a.out, len(want)))

    by_block = {}
    for n in want:
        b = block_of.get(n)
        by_block.setdefault(b["n"] if b else 0, []).append(n)
    dead = [b for b, ns in by_block.items()
            if b and len(ns) == len(block_of[ns[0]]["scenes"])]
    if dead:
        print("reels with no footage at all: %s" % sorted(dead))
        print("Those cannot be built until their scenes exist. If they never will,")
        print("set publish_as in project.json so the published run has no gap.")


if __name__ == "__main__":
    main()
