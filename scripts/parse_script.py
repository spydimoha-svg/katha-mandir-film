"""
Parse the shooting script into script.json, and scaffold project.json.

The expected shape is the Katha Mandir template: a .docx with "SCENE n"
headings, scenes grouped into fixed-size blocks where the last scene of each
block is marked (HOOK), and a "Shorts cut list" naming each block. None of that
is required though. If the cut list is missing the blocks still get inferred
from the block size, and if the HOOK markers are missing the last scene of each
block is assumed to be the hook, which is what the template means anyway.

Whatever it had to infer is printed, so a reformatted script degrades into a
warning rather than a wrong edit.

Usage:
    python parse_script.py --root <project dir> [--script FILE] [--clips DIR]
                           [--block-size 6]
"""

import argparse
import glob
import html
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmlib


def read_text(path):
    if path.lower().endswith(".docx"):
        x = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        x = x.replace("</w:p>", "\n")
        x = re.sub(r"<w:tab[^>]*/>", "\t", x)
        x = re.sub(r"<w:br[^>]*/>", "\n", x)
        return html.unescape(re.sub(r"<[^>]+>", "", x))
    return open(path, encoding="utf-8").read()


_QUOTES = "“”‘’\"'"


def parse_dialogue(block):
    """[{speaker, to, tamil, roman}] from the DIALOGUE section of a scene block.

    Two speaker notations are in use across these scripts and both reach the
    parser unchanged, so both are read here rather than asking whoever wrote the
    script to normalise it:

        KUMARAN: <tamil>            a Latin speaker name and a colon
        (<romanisation>)

        Tamil name -> Tamil name    a Tamil speaker, an arrow, an addressee
        "<tamil>"                   the line itself, usually quoted
        (<romanisation>)

    A speaker line with no Tamil (a stage direction like "(silence)") is kept
    with empty tamil so caption timing still accounts for the beat.
    """
    m = re.search(r"DIALOGUE[^\n]*?:?[ \t]*\n(.*?)(?:\nNEGATIVE:|\nDuration|\Z)",
                  block, re.S)
    if not m:
        return parse_body(block)[0]
    out, pending = [], None
    for raw in m.group(1).split("\n"):
        line = raw.strip()
        if not line:
            continue
        # a parenthesised line closes the current speech as its romanisation
        if re.match(r"^\(.*\)$", line) and pending is not None and pending["tamil"]:
            pending["roman"] = line.strip("()").strip()
            out.append(pending)
            pending = None
            continue
        arrow = re.match(r"^(.{1,40}?)\s*(?:→|->)\s*(.{1,40})$", line)
        sp = None if arrow else re.match(r"^([A-Z][A-Z À-ɏ]{1,24}):\s*(.*)$", line)
        if arrow or sp:
            if pending is not None:
                out.append(pending)
            if arrow:
                pending = {"speaker": arrow.group(1).strip(),
                           "to": arrow.group(2).strip(), "tamil": "", "roman": ""}
            else:
                pending = {"speaker": sp.group(1).strip(), "to": "",
                           "tamil": sp.group(2).strip(), "roman": ""}
            continue
        if pending is not None:
            pending["tamil"] = (pending["tamil"] + " " + line.strip(_QUOTES)).strip()
    if pending is not None:
        out.append(pending)
    return out


_TAMIL = re.compile(r"[஀-௿]")


_ARROW = re.compile(r"^(.{1,40}?)\s*(?:→|->)\s*(.{1,40})$")


_DIALOGUE_HEAD = re.compile(r"(?m)^[ 	]*DIALOGUE")


def is_arrow_script(text):
    """True for the heading-less arrow format: "name -> addressee" speakers.

    Decided once for the whole document and passed down, for two reasons that
    both showed up on the first script in this format.

    A scene with no dialogue contains no arrow, so a per-scene decision reads
    those seven scenes as a different format and loses their action line -
    which in a heading-less script is the only thing the clip mapper has to
    score filenames against.

    And the arrow notation is not exclusive to this format: format 2 writes its
    speakers the same way but keeps SETTING/ACTION/DIALOGUE headings, so the
    presence of headings is what tells the two apart. Without that check a
    format 2 scene body gets read as dialogue in full, prompt text included.

    Matching per line rather than with one search over the text, because
    _ARROW is anchored and this module does not use re.M.
    """
    lines = text.split("\n")
    return (any(_ARROW.match(l.strip()) for l in lines)
            and not _DIALOGUE_HEAD.search(text))


def parse_body(block, arrow=None):
    """(dialogue, stage directions) for a scene body with no field headings.

    Two heading-less formats are in use and they need different readers. Pass
    `arrow` from `is_arrow_script` on the whole document; it is only detected
    from the block itself when a caller has no document to hand.
    """
    if arrow is None:
        arrow = is_arrow_script(block)
    return (parse_arrow_body(block) if arrow else parse_screenplay(block))


def parse_arrow_body(block):
    """([{speaker, to, tamil, roman}], stage directions) from an arrow script.

    The fourth notation, and the first with no parenthesised directions at all:

        Outside Arul and Selvi's home, early morning. Selvi arranges fresh
        kozhukattai for the temple annadanam.
        அருள → செல்வி
        "அம்மா... ஒண்ணு மட்டும் சாப்பிடட்டுமா?"
        (Amma... onnu mattum saapidattumaa?)

    So the description of the shot is a plain English sentence, and it is the
    only thing the clip mapper has to score filenames against. Anything that is
    neither an arrow, a quoted Tamil line nor a romanisation is that.

    `parse_screenplay` cannot be widened into this. There a parenthesised line
    of more than six words is a stage direction, and here every romanisation
    over six words is parenthesised too, so the two rules contradict.
    """
    out, pending, notes = [], None, []

    def flush():
        nonlocal pending
        if pending is not None:
            out.append(pending)
            pending = None

    for raw in block.split("\n"):
        line = raw.strip()
        if not line or re.match(r"^(?:NO DIALOGUE|Duration|\d+\s*sec)", line, re.I):
            continue
        arrow = _ARROW.match(line)
        if arrow:
            flush()
            pending = {"speaker": arrow.group(1).strip(),
                       "to": arrow.group(2).strip(), "tamil": "", "roman": ""}
            continue
        if re.match(r"^\(.*\)$", line):
            inner = line.strip("()").strip()
            # A parenthesised line here is the romanisation of the line above,
            # which is what closes a speech. With nobody speaking it is a note.
            if pending is not None and pending["tamil"]:
                pending["roman"] = inner
                flush()
            else:
                notes.append(inner)
            continue
        if _TAMIL.search(line):
            # A voice-over is labelled with a bare Tamil name and no arrow
            # ("கண்ணன் குரல்"), so an unquoted Tamil line with nobody
            # currently speaking is a speaker, not a line. Every spoken line in
            # this format is quoted.
            if pending is None and line[0] not in _QUOTES and len(line.split()) <= 4:
                pending = {"speaker": line, "to": "", "tamil": "", "roman": ""}
                continue
            if pending is None:
                continue
            pending["tamil"] = (pending["tamil"] + " " + line.strip(_QUOTES)).strip()
            continue
        notes.append(line)
    flush()
    return out, notes




def parse_screenplay(block):
    """([{speaker, to, tamil, roman}], stage directions) from a bare screenplay.

    The third notation these scripts arrive in, and the leanest: no field
    headings at all, just what a stage script looks like.

        (Riverbank, evening. Thenu sits alone holding the broken rope.)
        KANNAN
        (appearing beside her)
        கயிறு அறுந்துடுச்சா அக்கா?
        Kayiru arundhuduchaa akka?

    Tamil and its romanisation are told apart by script rather than by position,
    because a line is sometimes only romanised and sometimes only Tamil, and
    counting lines gets that wrong in both directions.

    The parenthesised lines are returned separately because they are the only
    description of what is on screen in this format. The clip mapper needs an
    ACTION to score filenames against, and here that is all there is.
    """
    out, pending, notes = [], None, []
    for raw in block.split("\n"):
        line = raw.strip()
        if not line or re.match(r"^\d+\s*sec", line):
            continue
        if re.match(r"^\(.*\)$", line):
            inner = line.strip("()").strip()
            # A short parenthetical after a speaker is a delivery note; a longer
            # one, or one with nobody speaking, is describing the shot.
            if pending is None or len(inner.split()) > 6:
                notes.append(inner)
            continue
        name = re.match(r"^([A-Z][A-Z'À-ɏ ]{1,24})$", line)
        if name:
            if pending is not None:
                out.append(pending)
            pending = {"speaker": " ".join(name.group(1).split()), "to": "",
                       "tamil": "", "roman": ""}
            continue
        if pending is None:
            continue
        # An inline direction, "*(to Sokkan)*", is written into the middle of a
        # spoken line. It is not spoken, so it must not reach a caption.
        line = re.sub(r"\*\([^)]*\)\*", " ", line).strip()
        if not line:
            continue
        key = "tamil" if _TAMIL.search(line) else "roman"
        pending[key] = (pending[key] + " " + line.strip(_QUOTES)).strip()
    if pending is not None:
        out.append(pending)
    # A beat written as a bare ellipsis is a silence, not a line to caption.
    for d in out:
        if d["tamil"].strip("….… ") == "":
            d["tamil"] = ""
    return out, notes


# Scripts differ in what they call the same field. The first name present wins,
# so a renamed heading degrades to the alternative rather than to blank.
_ALIASES = {"SETTING": ["SETTING", "ENVIRONMENT"]}


def field(block, key):
    for name in _ALIASES.get(key, [key]):
        m = re.search(name + r":(.*?)(?:\n\n|\n[A-Z]{4,}[^\n]*:|\Z)", block, re.S)
        if m and m.group(1).strip():
            return " ".join(m.group(1).split())
    return ""


def parse_cut_list(text):
    """{block number: {title, hook, first, last}} from the shorts cut list.

    When the cut list names its own scene range ("Short #7  Scenes 19-23") that
    is the author's decision about where the reels break, and it beats any block
    size this parser could assume. Real blocks are often uneven: a three-shot
    ending sequence belongs in one reel with the scenes leading into it, not
    split across two. So a written range is captured and used.

    Titles and hooks are merged across every mention of a block, because a
    document usually states the range in one place and the title in another.
    """
    out = {}

    def slot(n):
        return out.setdefault(int(n),
                              {"title": "", "hook": "", "first": None, "last": None})

    for m in re.finditer(r"(?:Short|Reel|Part)\s*#?(\d+)\s*[·•|\-]?\s*"
                         r"Scenes?\s*(\d+)[A-Za-z]?\s*[-–]\s*(\d+)[A-Za-z]?"
                         r"([^\n]*)", text, re.I):
        b = slot(m.group(1))
        b["first"], b["last"] = int(m.group(2)), int(m.group(3))
        rest = m.group(4)
        h = re.search(r"(?:ends?\s+on|hook)\s*:\s*(.+)$", rest, re.I)
        if h:
            if not b["hook"]:
                b["hook"] = h.group(1).strip().strip(_QUOTES)
            rest = rest[:h.start()]
        for piece in re.split(r"[·•|]", rest):
            piece = piece.strip().strip(_QUOTES)
            if piece and not b["title"] and not re.match(r"(?i)ends?\s+on|hook", piece):
                b["title"] = piece

    for m in re.finditer(r"(?m)^\s*(?:BLOCK|PART)\s+(\d+)\s*[—–-]\s*(.+?)\s*$", text):
        b = slot(m.group(1))
        t = m.group(2).strip().strip(_QUOTES)
        if t and not b["title"]:
            b["title"] = t

    # legacy form: "Short #1 · TITLE · hook: ..." with no scene range
    for m in re.finditer(r"(?:Short|Reel|Part)\s*#?(\d+)[^\n]*?"
                         r"[·•|]\s*([A-Z][A-Z !?'À-ɏ]{2,40})", text):
        b = slot(m.group(1))
        if not b["title"]:
            b["title"] = m.group(2).strip()
    return out


_PLACE = re.compile(
    r"\b(shop|stall|yard|grove|riverbank|river|square|street|temple|house|home|"
    r"doorway|counter|road|lane|bank|banyan|palm|field|hut|courtyard|kitchen|"
    r"stone|tree|well|steps|veranda|platform|shutter|gate|"
    r"night|morning|dawn|dusk|evening|noon|midday|festival)\b", re.I)

_ROLES = ("boy", "girl", "man", "woman", "child", "grandmother", "grandfather",
          "mother", "father", "elder", "landlord", "watchman", "guard",
          "daughter", "son", "priest", "farmer", "cowherd", "villager",
          "teacher", "servant", "merchant", "dog", "cow", "calf")


def parse_cast(text):
    """{CHARACTER NAME: role word} from the character list or the style lock.

    Both layouts in use are read, since the same information is written either
    way depending on who drafted the script:

        KUMARAN: boy, 12, lean, deep warm brown skin, ...
        MURUGAN - village boy, 10 - thin, torn faded blue shirt, ...

    This is exactly the bridge the clip mapper needs: Flow names its exports by
    role ("Boy_striking_temple_bell") while the script names its people. Reading
    it out of the script keeps the mapper working on a new story rather than on
    one hardcoded cast.
    """
    out, desc = {}, {}
    for m in re.finditer(r"(?m)^\s*([A-Z][A-Z'\-“”\" ]{2,34}?)\s*[:—–-]\s*"
                         r"(?:an?\s+)?([a-z][a-z\- ]{0,40}?)\s*(?:,|\bwho\b)", text):
        role = m.group(2).split()[-1].lower()
        if role not in _ROLES:
            continue
        # The rest of that character's line is their look. A prompt for an
        # unshot scene needs it, or the generator invents a different person
        # for a character who is already on screen in twenty other clips.
        line = text[m.start():].split("\n", 1)[0].strip()
        body = re.sub(r"^\s*[A-Z][A-Z'\-“”\" ]{2,34}?\s*[:—–-]\s*", "", line)
        name = " ".join(m.group(1).split())
        # A nickname in quotes is the name the dialogue actually uses
        # (THENMOZHI "THENU"), so register both spellings against the role.
        for part in [name] + re.findall(r"[“”\"]([^“”\"]+)", name):
            key = re.sub(r"[“”\"]", "", part).strip()
            out.setdefault(key, role)
            desc.setdefault(key, body)
    return out, desc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--script")
    ap.add_argument("--clips")
    ap.add_argument("--block-size", type=int, default=6)
    a = ap.parse_args()
    root = os.path.abspath(a.root)

    script = a.script
    if not script:
        cands = [p for p in glob.glob(os.path.join(root, "*.docx"))
                 if not os.path.basename(p).startswith("~$")]
        if len(cands) != 1:
            raise SystemExit("pass --script; found %d .docx files in %s"
                             % (len(cands), root))
        script = cands[0]

    clips = a.clips
    if not clips:
        dirs = [d for d in glob.glob(os.path.join(root, "*"))
                if os.path.isdir(d) and glob.glob(os.path.join(d, "*.mp4"))]
        if len(dirs) != 1:
            raise SystemExit("pass --clips; found %d folders with mp4s in %s"
                             % (len(dirs), root))
        clips = dirs[0]

    text = read_text(script)
    # "NO DIALOGUE" contains "DIALOGUE", so parse_dialogue's heading search
    # matches it and returns the empty remainder of the scene. Harmless in the
    # heading formats, wrong in this one, which is the other reason the format
    # is settled here rather than inside the per-scene reader.
    arrow_script = is_arrow_script(text)
    cast, cast_desc = parse_cast(text)
    parts = re.split(r"(?m)^SCENE (\d+)(.*)$", text)
    if len(parts) < 4:
        raise SystemExit("no 'SCENE n' headings found in " + script)

    inferred = []
    scenes = {}
    for i in range(1, len(parts), 3):
        n = int(parts[i])
        suffix, body = parts[i + 1], parts[i + 2]
        # A scene's body runs to the next SCENE heading, which sweeps up the
        # next block's title and any trailing notes. Those lines are Tamil too,
        # so left in they get read as the last speaker's dialogue.
        # An END CARD belongs to the film, not to the last scene: left in, its
        # two Tamil lines are read as one more speaker and one more line of
        # dialogue, and its English gloss lands in the scene action text.
        stop = re.search(r"(?m)^\s*(?:BLOCK|Short\s*#|Post order|A note on"
                         r"|(?:FINAL\s+)?END\s+CARD)\b", body)
        if stop:
            body = body[:stop.start()]
        end = body.find("Duration")
        prompt = body[:body.find("\n", end)] if end != -1 else body
        dur = re.search(r"Duration\s+([\d.]+)\s*second", body)
        dialogue, notes = (parse_arrow_body(body) if arrow_script
                           else (parse_dialogue(body), None))
        action, chars = field(body, "ACTION"), field(body, "CHARACTERS")
        if not action:
            # No ACTION heading: a heading-less form, where the directions are
            # the only description of the shot.
            if notes is None:
                notes = parse_body(body, arrow_script)[1]
            action = " ".join(notes)
        if not chars:
            chars = ", ".join(dict.fromkeys(d["speaker"] for d in dialogue))
        scenes[n] = {
            "n": n,
            "hook": "HOOK" in suffix.upper(),
            "action": action,
            "setting": field(body, "SETTING"),
            "camera": field(body, "CAMERA"),
            "emotion": field(body, "EMOTION"),
            "characters": chars,
            "dialogue": dialogue,
            "script_seconds": float(dur.group(1)) if dur else None,
            "prompt": prompt.strip("\n"),
        }

    nums = sorted(scenes)
    bs = a.block_size
    cut = parse_cut_list(text)
    if not cut:
        inferred.append("no 'Shorts cut list'; blocks numbered without titles")

    # Ranges the cut list stated itself, if they cover every scene exactly once.
    # Partial coverage is not used at all: half the reels following the author
    # and half following an assumed block size would be worse than either.
    ranged = sorted((b for b in cut.values() if b["first"] and b["last"]),
                    key=lambda b: b["first"])
    covered = [n for b in ranged for n in range(b["first"], b["last"] + 1)]
    use_ranges = bool(ranged) and covered == nums

    blocks = []
    if use_ranges:
        for i, b in enumerate(ranged, 1):
            n = next(k for k, v in cut.items() if v is b)
            blocks.append({"n": n, "title": b["title"] or "PART %d" % n,
                           "hook": b["hook"],
                           "scenes": list(range(b["first"], b["last"] + 1))})
        sizes = sorted({len(x["scenes"]) for x in blocks})
        inferred.append("blocks taken from the cut list: %d reels, %s scenes each"
                        % (len(blocks), "/".join(str(s) for s in sizes)))
    else:
        if ranged:
            inferred.append("cut list ranges cover %d scenes but the script has %d, "
                            "so they were ignored and blocks of %d used instead"
                            % (len(covered), len(nums), bs))
        for b in range(1, (max(nums) + bs - 1) // bs + 1):
            rng = [n for n in range((b - 1) * bs + 1, b * bs + 1) if n in scenes]
            if not rng:
                continue
            c = cut.get(b) or {}
            blocks.append({"n": b, "title": c.get("title") or "PART %d" % b,
                           "hook": c.get("hook", ""), "scenes": rng})

    if not any(s["hook"] for s in scenes.values()):
        inferred.append("no (HOOK) markers; treating the last scene of each block "
                        "as the hook")
        for blk in blocks:
            scenes[blk["scenes"][-1]]["hook"] = True

    # A screenplay states where it is only when the place changes, so carry the
    # last stated place forward. Without it, every scene that is only dialogue
    # has no location at all, and the prompt written for an unshot one puts the
    # characters somewhere the surrounding footage is not.
    # The script's own Location paragraph, if it has one, is the standing
    # answer for every scene before the first that states a place of its own.
    m = re.search(r"(?m)^\s*Location\s*$\s*\n(.+)", text)
    place = " ".join(m.group(1).split()) if m else ""
    default_place = place
    for n in nums:
        s = scenes[n]
        first = s["action"].split(".")[0] if s["action"] else ""
        if first and _PLACE.search(first):
            place = first.strip()
        s["location"] = s["setting"] or place

    kmlib.save_json({"source": os.path.basename(script),
                     "cast": cast,
                     "cast_desc": cast_desc,
                     "location_default": default_place,
                     "scene_count": len(scenes),
                     "scene_numbers": nums,
                     "block_size": bs,
                     "blocks": blocks,
                     "inferred": inferred,
                     "scenes": {str(k): v for k, v in scenes.items()}},
                    os.path.join(root, "script.json"))

    proj_path = os.path.join(root, "project.json")
    if not os.path.exists(proj_path):
        stem = re.sub(r"[^A-Za-z0-9]+", "_",
                      os.path.splitext(os.path.basename(script))[0]).strip("_").upper()
        kmlib.save_json({
            "name": stem.lower(),
            "script": os.path.relpath(script, root).replace("\\", "/"),
            "clip_dir": os.path.relpath(clips, root).replace("\\", "/"),
            # Fill these in before rendering. title_tamil is LOGICAL order;
            # the render reorders it for display, do not pre-reorder it here.
            "title_tamil": "",
            "title_tamil_line2": "",
            "title_latin": stem.replace("_", " "),
            "series_line": "Kannan Series",
            "film_out": stem + "_longform_v1.mp4",
            "publish_as": {},
            "reel_titles": {},
            # Where Flow stamps its provenance mark. The default is measured;
            # re-measure and override only if a future export moves it.
            "provenance_box": kmlib.PROVENANCE_BOX,
            # Sidecar files both times. Captions burned into a reel cannot be
            # switched off, cannot be indexed, and cannot be corrected without
            # a re-render, which is why this channel stopped doing it.
            "captions": {"film": "srt", "reels": "srt"},
        }, proj_path)
        print("scaffolded project.json - fill in title_tamil and title_latin")

    print("script: %s" % os.path.basename(script))
    print("scenes: %d (%d..%d)  blocks: %d of %d"
          % (len(scenes), nums[0], nums[-1], len(blocks), bs))
    gaps = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
    if gaps:
        print("gaps in scene numbering: %s" % gaps)
    for line in inferred:
        print("inferred: " + line)
    print("clips dir: %s (%d mp4)"
          % (os.path.relpath(clips, root), len(glob.glob(os.path.join(clips, "*.mp4")))))


if __name__ == "__main__":
    main()
