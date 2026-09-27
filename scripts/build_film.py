"""
Assemble the long-form film from the mapped clips.

Reads edl.json (scene -> clip) and fixes.json (per-scene picture repairs),
encodes each scene to one uniform intermediate, then stream-copy concatenates.

Two structural choices worth keeping:

  Repairs are applied here, at the same encode that reads the source, so the
  film stays at one generation of compression. Stamping them onto a finished
  render instead costs a second generation for nothing.

  Segments are cached per scene. Re-running after changing one repair or adding
  one clip re-encodes only what changed, which turns a 10 minute rebuild into a
  20 second one. That matters because you will rebuild more than you expect.

Usage:
    python build_film.py --root <project dir> [--no-title] [--no-watermark]
                         [--force] [--scenes 6,37]
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib
import tamil_text

WM_WIDTH = 96          # 7.5% of a 1280 frame
WM_MARGIN = 44
TITLE_SECONDS = 5.0


def build_title(path, cfg, fonts, work):
    """Opening card: warm ground, Tamil title, rule, romanised title, series line.

    The Tamil is overlaid as a shaped PNG rather than drawn with drawtext, which
    loses the -u vowel signs entirely; see tamil_text.py. Positions below are the
    ones used across the series, so the card stays identical film to film.
    """
    d = TITLE_SECONDS
    b = kmlib.BRAND
    root = cfg["root"]
    specs = []
    for key, size, colour in (("title_tamil", 96, b["gold"]),
                              ("title_tamil_line2", 104, b["gold"])):
        txt = (cfg.get(key) or "").strip()
        if txt:
            specs.append({"key": key, "text": txt, "size": size, "colour": colour})
    # Two lines means the first is a smaller lead-in above the main title.
    if len(specs) == 2:
        specs[0].update(size=56, colour=b["gold_dim"])
        ys = [186, 256]
        rule_y, latin_y, sub_y, latin_size = 418, 456, 520, 29
    elif specs:
        ys = [228]
        rule_y, latin_y, sub_y, latin_size = 376, 418, 486, 38
    else:
        ys = []
        rule_y, latin_y, sub_y, latin_size = 330, 370, 440, 44

    rendered = tamil_text.render_batch(
        [{"id": s["key"], "text": s["text"], "size": s["size"],
          "color": s["colour"].replace("0x", "#"), "max_width": 1160,
          "max_lines": 1, "min_size": int(s["size"] * 0.7)} for s in specs],
        os.path.join(work, "titletext"))

    latin = kmlib.rel(fonts["latin"], root)
    imgs, over = [], []
    for s, y in zip(specs, ys):
        ln = rendered[s["key"]]["lines"][0]
        pad = int(round(rendered[s["key"]]["size"] * 0.45))
        imgs.append(kmlib.rel(ln["file"], root))
        over.append((y - pad))

    vf = []
    vf.append("drawbox=x=(iw-220)/2:y=%d:w=220:h=2:color=%s@0.85:t=fill" % (rule_y, b["rule"]))
    # Letterspacing by hand: drawtext has no tracking control. Single space
    # between letters, three between words - matching this exactly matters
    # because the card is the one frame every video in the series opens on.
    spaced = "   ".join(" ".join(w) for w in cfg.get("title_latin", "").split())
    if cfg.get("title_latin"):
        vf.append("drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=%s:x=(w-tw)/2:y=%d"
                  % (latin, kmlib.dt_escape(spaced), latin_size, b["cream"], latin_y))
    if cfg.get("series_line"):
        vf.append("drawtext=fontfile=%s:text='%s':fontsize=22:fontcolor=%s:x=(w-tw)/2:y=%d"
                  % (latin, kmlib.dt_escape(cfg["series_line"]), b["brass"], sub_y))
    # The title images go on before the fades, so the whole card fades as one.
    args = ["-f", "lavfi", "-i", "color=c=%s:s=1280x720:r=24:d=%s" % (b["ground"], d),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=%s" % d]
    for p in imgs:
        args += ["-loop", "1", "-t", str(d), "-i", p]
    steps = ["[0:v]%s[c0]" % ",".join(vf)]
    for i, y in enumerate(over):
        steps.append("[c%d][%d:v]overlay=x=(W-w)/2:y=%d[c%d]" % (i, i + 2, y, i + 1))
    steps.append("[c%d]fade=t=in:st=0:d=0.8,fade=t=out:st=%.2f:d=0.8[v]"
                 % (len(over), d - 0.8))
    kmlib.run(args + ["-filter_complex", ";".join(steps),
                      "-map", "[v]", "-map", "1:a", "-t", d]
              + kmlib.VENC + kmlib.AENC + [path], cwd=cfg["root"])


def wm_position(cfg, fix, scene):
    """Where the logo goes on this scene, as (x, y), plus a note for the report.

    The generator stamps a fixed provenance sparkle into every clip. Left in the
    default corner the round logo sits beside it rather than on it, and its
    transparent corner leaves the sparkle showing, unevenly, shot to shot. So
    when `provenance_box` is set in project.json the logo is centred on that mark
    instead, after following it through whatever repair the scene carries.

    Three outcomes, all reported rather than silently guessed:
      covered   the logo is placed over the mark and provably covers it
      gone      a repair crop already removed the mark; use the same position
                anyway so the logo does not move around between scenes
      EXPOSED   the mark survives somewhere the logo cannot reach. Fix the
                repair; do not ship it.
    """
    default = (1280 - WM_WIDTH - WM_MARGIN, 720 - WM_WIDTH - WM_MARGIN)
    box = cfg.get("provenance_box", kmlib.PROVENANCE_BOX)
    if not box:
        return default, ""
    home = kmlib.cover_position(box, WM_WIDTH, (1280, 720))
    mapped = kmlib.map_box(box, fix)
    if mapped == "gone":
        return home or default, "mark removed by the repair"
    if mapped is None:
        return home or default, ("scene %d: repair geometry could not be followed, "
                                 "watermark placed for an unrepaired frame" % scene)
    pos = kmlib.cover_position(mapped, WM_WIDTH, (1280, 720))
    if pos is None:
        return (home or default), ("scene %d: EXPOSED, the mark moves to %s where a "
                                   "%dpx round logo cannot cover it"
                                   % (scene, tuple(int(v) for v in mapped), WM_WIDTH))
    return pos, ""


def build_segment(src, dst, fix, watermark, root, wm_xy):
    """One scene at the shared spec. Repairs run before the watermark goes on,
    so a zoom-crop repair never rescales the logo.

    Runs from the project root because the watermark and font paths inside a
    filtergraph are relative - a Windows drive letter in a filtergraph is an
    escaping minefield, so kmlib.rel strips it and the cwd supplies the rest.
    """
    d = kmlib.duration(src)
    af = ("afade=t=in:st=0:d=0.04,afade=t=out:st=%.3f:d=0.04,apad" % (d - 0.04))
    # An off-spec clip is put on the spec before anything else, so the repair
    # that follows is measured in the same coordinates as every other scene.
    chain = kmlib.join_chain(kmlib.spec_prefix(src), fix)
    args = ["-i", src]
    if watermark:
        head = ("[0:v]%s[b];" % chain) if chain else "[0:v]null[b];"
        args += ["-i", watermark, "-filter_complex",
                 head + "[b][1:v]overlay=%d:%d[v]" % wm_xy,
                 "-map", "[v]", "-map", "0:a"]
    elif chain:
        args += ["-vf", chain]
    args += ["-af", af, "-t", "%.3f" % d] + kmlib.VENC + kmlib.AENC + [dst]
    kmlib.run(args, cwd=root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--no-title", action="store_true")
    ap.add_argument("--no-watermark", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--scenes", help="rebuild only these scenes, then re-concat")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    cfg = kmlib.load_project(root)
    work = cfg["work"]
    segdir = os.path.join(work, "segments")
    os.makedirs(segdir, exist_ok=True)

    edl = kmlib.load_json(os.path.join(root, "edl.json"))
    fixes = {}
    fpath = os.path.join(root, "fixes.json")
    if os.path.exists(fpath):
        fixes = {int(k): v for k, v in kmlib.load_json(fpath).items()}
    # A segment is stale when the repair changed, not only when the clip did.
    # Keyed on the source alone, editing a repair rebuilt nothing and the film
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

    fonts = kmlib.prepare_fonts(os.path.join(work, "assets"))
    watermark = None
    if not a.no_watermark:
        wm = os.path.join(work, "assets", "watermark_round_%d.png" % WM_WIDTH)
        if not os.path.exists(wm):
            subprocess.run([sys.executable,
                            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "make_watermark.py"),
                            "--width", str(WM_WIDTH),
                            "--out", os.path.join(work, "assets")], check=True)
        watermark = kmlib.rel(wm, root)

    only = {int(x) for x in a.scenes.split(",")} if a.scenes else None
    notes = []
    entries = sorted(edl["map"], key=lambda m: m["scene"])
    order = []

    if not a.no_title:
        t = os.path.join(segdir, "000_title.mp4")
        # Fingerprinted like the scenes are, and for the same reason: changing
        # the title in project.json changes no file the cache was watching, so
        # the card silently stayed on the old name. Renaming the film is a
        # normal request here, not an edge case.
        card = "|".join(str(cfg.get(k, "")) for k in
                        ("title_tamil", "title_tamil_line2", "title_latin",
                         "series_line"))
        cside = t + ".card"
        try:
            cgot = open(cside, encoding="utf-8").read()
        except OSError:
            cgot = None
        # No sidecar means the card's text is unknown, so it is rebuilt once
        # rather than stamped with whatever the title happens to be now. The
        # card takes seconds; a wrong name on it is on every frame of the open.
        if a.force or not os.path.exists(t) or cgot != card:
            print("title card")
            build_title(t, cfg, fonts, os.path.join(work, "assets"))
        if cgot != card:
            open(cside, "w", encoding="utf-8").write(card)
        order.append(t)

    clipdir = os.path.join(root, cfg["clip_dir"])
    for i, m in enumerate(entries, 1):
        src = os.path.join(clipdir, m["file"])
        if not os.path.exists(src):
            raise SystemExit("missing clip for scene %s: %s" % (m["scene"], m["file"]))
        dst = os.path.join(segdir, "%03d_s%02d.mp4" % (i, m["scene"]))
        want, got, side = fix_state(dst, m["scene"])
        stale = (a.force or not os.path.exists(dst)
                 or os.path.getmtime(dst) < os.path.getmtime(src)
                 or want != got
                 or (only and m["scene"] in only))
        fix = fixes.get(m["scene"])
        wm_xy, note = wm_position(cfg, fix, m["scene"])
        if note:
            notes.append(note)
        if stale:
            print("[%2d/%d] scene %-3d %s%s"
                  % (i, len(entries), m["scene"], m["file"][:46], "  [fix]" if fix else ""))
            build_segment(src, dst, fix, watermark, root, wm_xy)
            stamp(side, want)
        order.append(dst)

    listfile = os.path.join(work, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for p in order:
            fh.write("file '%s'\n" % p.replace("\\", "/"))
    out = os.path.join(root, cfg["film_out"])
    print("concatenating %d segments" % len(order))
    kmlib.run(["-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy",
               "-movflags", "+faststart", out])

    total = kmlib.duration(out)
    print("\nwrote %s" % cfg["film_out"])
    for n in dict.fromkeys(notes):
        print("  " + n)
    print("runtime %d:%02d  (%d scenes%s%s)"
          % (int(total // 60), int(total % 60), len(entries),
             "" if a.no_title else " + title card",
             "" if a.no_watermark else ", watermarked"))


if __name__ == "__main__":
    main()
