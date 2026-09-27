"""
Build captions for the film and the reels.

The script already holds the Tamil dialogue per scene, and the EDL fixes every
scene's exact position and length, so caption timing can be derived rather than
transcribed. Within a scene, lines are spread across the clip weighted by their
length, which tracks delivery closely enough at this shot length.

English is not in the script, so `scaffold` leaves it blank for you to fill.
Keep the English short. It sits under the Tamil inside a reel's lower bar, and a
line that wraps to three rows there is unreadable at the size shorts are watched.

Subcommands:
    scaffold  captions.json with Tamil and timings filled, English blank
    srt       long-form .srt, timed against the film (title card offset included)
    docs      sidecar caption files for the film AND every reel: one .srt per
              language plus a .docx to read and correct. This is how captions
              ship; nothing is burned into the picture.
    check     report blank English, over-long lines, and timing overruns

Usage:
    python captions.py --root <project dir> scaffold
    python captions.py --root <project dir> srt
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib

TITLE_SECONDS = 5.0
LEAD_IN = 0.15
GAP = 0.08
MAX_TAMIL = 42        # characters before a line gets hard to read in a reel bar
MAX_ENGLISH = 52


def scene_positions(root, cfg, edl):
    """{scene: (start_in_film, duration)}, in EDL order after the title card."""
    clipdir = os.path.join(root, cfg["clip_dir"])
    t = 0.0 if cfg.get("no_title") else TITLE_SECONDS
    pos = {}
    for m in sorted(edl["map"], key=lambda m: m["scene"]):
        d = kmlib.duration(os.path.join(clipdir, m["file"]))
        pos[m["scene"]] = (t, d)
        t += d
    return pos


def lay_out(lines, dur):
    """Spread lines across a scene, weighted by Tamil length."""
    if not lines:
        return []
    weights = [max(len(l.get("tamil", "")), 8) for l in lines]
    total = float(sum(weights))
    span = max(dur - LEAD_IN, 0.5)
    out, t = [], LEAD_IN
    for l, w in zip(lines, weights):
        d = max(span * w / total - GAP, 0.6)
        out.append(dict(l, start=round(t, 3), end=round(min(t + d, dur), 3)))
        t += d + GAP
    return out


def ts(x):
    """SRT timestamp: hh:mm:ss,mmm"""
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def cmd_scaffold(root, cfg, a):
    script = kmlib.load_json(os.path.join(root, "script.json"))
    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    pos = scene_positions(root, cfg, edl)
    out, path = {}, os.path.join(root, "captions.json")
    existing = kmlib.load_json(path).get("scenes", {}) if os.path.exists(path) else {}
    for scene, (_, dur) in sorted(pos.items()):
        src = script["scenes"].get(str(scene), {}).get("dialogue", [])
        prev = {l.get("tamil", ""): l.get("english", "")
                for l in existing.get(str(scene), {}).get("lines", [])}
        lines = [{"speaker": d["speaker"], "tamil": d["tamil"],
                  "english": prev.get(d["tamil"], "")} for d in src if d.get("tamil")]
        out[str(scene)] = {"duration": round(dur, 3), "lines": lay_out(lines, dur)}
    kmlib.save_json({"scenes": out}, path)
    n = sum(len(v["lines"]) for v in out.values())
    blank = sum(1 for v in out.values() for l in v["lines"] if not l["english"])
    print("captions.json: %d scenes, %d lines, %d awaiting English" % (len(out), n, blank))
    print("Fill in the english fields, then run `srt`, then build the reels.")


def cmd_srt(root, cfg, a):
    caps = kmlib.load_json(os.path.join(root, "captions.json"))["scenes"]
    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    pos = scene_positions(root, cfg, edl)
    rows = []
    for scene, (start, _) in sorted(pos.items()):
        for l in caps.get(str(scene), {}).get("lines", []):
            text = l["tamil"]
            if l.get("english"):
                text += "\n" + l["english"]
            rows.append((start + l["start"], start + l["end"], text))
    rows.sort()
    out = os.path.splitext(os.path.join(root, cfg["film_out"]))[0] + ".srt"
    with open(out, "w", encoding="utf-8") as fh:
        for i, (s, e, t) in enumerate(rows, 1):
            fh.write("%d\n%s --> %s\n%s\n\n" % (i, ts(s), ts(e), t))
    print("wrote %s (%d cues)" % (os.path.basename(out), len(rows)))
    print("Upload this alongside the film; do not burn it in, so viewers can")
    print("switch it off and YouTube can index the text.")


def reel_rows(root, cfg, script, edl, caps):
    """{block: [(start, end, tamil, english)]} timed from each reel's own zero,
    because a reel is uploaded as its own video with its own caption track."""
    clipdir = os.path.join(root, cfg["clip_dir"])
    have = {m["scene"]: m["file"] for m in edl["map"]}
    out = {}
    for blk in script["blocks"]:
        scenes = [s for s in blk["scenes"] if s in have]
        if not scenes:
            continue
        # A reel with a cold open is shorter than its scenes add up to, and the
        # trimmed scene's own lines move with it. Read from the same
        # project.json entry the builder reads, or every cue after the opening
        # is late by however much was trimmed and the whole track drifts.
        co = kmlib.cold_open(cfg, blk["n"])
        t, rows = 0.0, []
        for s in scenes:
            skip = co[1] if (co and s == co[0]) else 0.0
            d = kmlib.duration(os.path.join(clipdir, have[s])) - skip
            for l in caps.get(str(s), {}).get("lines", []):
                start, end = l["start"] - skip, l["end"] - skip
                # In a trimmed scene, drop any cue with only a sliver left. The
                # scaffold's timings are text lengths spread across the clip
                # rather than measured speech, so a cue that ends just after the
                # cut is a straggler from that imprecision, not a line anyone
                # hears: part 9 was captioning the line BEFORE its cold open for
                # the first 0.7s. Only applied when something was trimmed, since
                # lay_out floors a real short line at 0.6s and this would
                # otherwise delete "ஆமா." from every reel.
                if skip and end - max(start, 0.0) < 0.8:
                    continue
                if end <= 0.05:
                    continue            # spoken before the cold open starts
                rows.append((t + max(start, 0.0), t + end,
                             l.get("tamil", ""), l.get("english", "")))
            t += d
        out[blk["n"]] = rows
    return out


def write_srt(path, rows, field):
    """One language per file. YouTube assigns a caption track to a language, so
    a file mixing Tamil and English cannot be filed as either."""
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for s, e, ta, en in rows:
            text = (ta if field == "tamil" else en).strip()
            if not text:
                continue
            n += 1
            fh.write("%d\n%s --> %s\n%s\n\n" % (n, ts(s), ts(e), text))
    return n


def sbv_ts(x):
    """SubViewer timestamp: h:mm:ss.mmm, no zero padding on the hour."""
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%d:%02d:%02d.%03d" % (h, m, s, ms)


def write_sbv(path, rows, field):
    """YouTube's own native caption format.

    Worth writing alongside the .srt because it is the format YouTube itself
    exports, it carries no cue numbers to get out of step, and it is the one to
    reach for the moment Studio says it cannot parse something.
    """
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for s, e, ta, en in rows:
            text = (ta if field == "tamil" else en).strip()
            if not text:
                continue
            n += 1
            fh.write("%s,%s\n%s\n\n" % (sbv_ts(s), sbv_ts(e), text))
    return n


def write_docx(path, title, rows):
    """A readable copy for review, not the upload format.

    It exists so wording can be checked and corrected by someone who is not
    going to open a subtitle file. YouTube takes the .srt.
    """
    from docx import Document
    d = Document()
    d.add_heading(title, level=1)
    d.add_paragraph("Captions for review. Upload the matching .srt files to "
                    "YouTube, one per language. This document is a reference "
                    "copy and is not a caption format.")
    t = d.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for i, h in enumerate(("Time", "Tamil", "English")):
        t.rows[0].cells[i].text = h
    for s, e, ta, en in rows:
        c = t.add_row().cells
        c[0].text = "%s - %s" % (ts(s)[:-4], ts(e)[:-4])
        c[1].text = ta
        c[2].text = en
    d.save(path)


def cmd_docs(root, cfg, a):
    """Sidecar caption files for the film and every reel.

    Captions ship as files rather than burned into the picture: the viewer can
    switch them off or auto-translate them, YouTube indexes the text, and the
    wording can be corrected later without re-rendering a single frame.
    """
    caps = kmlib.load_json(os.path.join(root, "captions.json"))["scenes"]
    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    script = kmlib.load_json(os.path.join(root, "script.json"))
    outdir = os.path.join(root, "captions")
    os.makedirs(outdir, exist_ok=True)

    pos = scene_positions(root, cfg, edl)
    film = []
    for scene, (start, _) in sorted(pos.items()):
        for l in caps.get(str(scene), {}).get("lines", []):
            film.append((start + l["start"], start + l["end"],
                         l.get("tamil", ""), l.get("english", "")))
    film.sort()

    stem = os.path.splitext(os.path.basename(cfg["film_out"]))[0]
    targets = [(stem, cfg.get("title_latin") or stem, film)]
    publish_as = {int(k): int(v) for k, v in (cfg.get("publish_as") or {}).items()}
    titles = cfg.get("reel_titles") or {}
    for bn, rows in sorted(reel_rows(root, cfg, script, edl, caps).items()):
        pub = publish_as.get(bn, bn)
        blk = next(b for b in script["blocks"] if b["n"] == bn)
        name = titles.get(str(bn)) or blk["title"]
        safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
        targets.append(("REEL_%02d_%s" % (pub, safe),
                        "Part %d of %s" % (pub, cfg.get("title_latin") or ""), rows))

    # The .docx goes in its own folder. Left beside the subtitle files it gets
    # picked by mistake in an upload dialog, and the platform then reports that
    # it cannot parse the file, which reads like the captions are broken when
    # nothing is wrong with them.
    review = os.path.join(outdir, "review")
    os.makedirs(review, exist_ok=True)

    for st, title, rows in targets:
        # Single, unambiguous extension. A name like "film.ta.srt" is a valid
        # .srt, but it invites the same confusion, and the language is clearer
        # spelled out when someone is choosing a file in a hurry.
        ta = write_srt(os.path.join(outdir, st + "_Tamil.srt"), rows, "tamil")
        write_sbv(os.path.join(outdir, st + "_Tamil.sbv"), rows, "tamil")
        en = write_srt(os.path.join(outdir, st + "_English.srt"), rows, "english")
        write_sbv(os.path.join(outdir, st + "_English.sbv"), rows, "english")
        write_docx(os.path.join(review, st + ".docx"), title, rows)
        print("%-46s %2d Tamil / %2d English cues" % (st[:44], ta, en))
    print("\n%d subtitle files in %s, %d review documents in %s"
          % (len(glob.glob(os.path.join(outdir, "*.s*"))),
             os.path.relpath(outdir, root),
             len(glob.glob(os.path.join(review, "*"))),
             os.path.relpath(review, root)))
    print("Upload the _Tamil.srt as the Tamil track, the _English.srt as English.")
    print("If the platform refuses a .srt, upload the matching .sbv instead;")
    print("that is YouTube's own format and it accepts it when nothing else works.")
    print("Never upload anything from review/. Those are Word documents.")


def cmd_check(root, cfg, a):
    caps = kmlib.load_json(os.path.join(root, "captions.json"))["scenes"]
    bad = 0
    for scene in sorted(caps, key=int):
        v = caps[scene]
        for l in v["lines"]:
            issues = []
            if not l.get("english"):
                issues.append("no English")
            if len(l.get("tamil", "")) > MAX_TAMIL:
                issues.append("Tamil %d chars" % len(l["tamil"]))
            if len(l.get("english", "")) > MAX_ENGLISH:
                issues.append("English %d chars" % len(l["english"]))
            if l["end"] > v["duration"] + 0.01:
                issues.append("runs past end of scene")
            if issues:
                bad += 1
                print("  s%-3s %-9s %s" % (scene, l.get("speaker", ""), "; ".join(issues)))
    print("%d lines need attention" % bad if bad else "captions look clean")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scaffold", "srt", "docs", "check"):
        sub.add_parser(name)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    {"scaffold": cmd_scaffold, "srt": cmd_srt, "docs": cmd_docs,
     "check": cmd_check}[a.cmd](root, cfg, a)


if __name__ == "__main__":
    main()
