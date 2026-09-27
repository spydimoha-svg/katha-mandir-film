"""
Turn the Katha Mandir logo into watermark PNGs at a given width.

The logo is a circular emblem on a flat white square, and the circle runs edge to
edge. Two variants come out:

  watermark_box_<w>.png    the square as-is, white corners left in
  watermark_round_<w>.png  a circular alpha mask, so only the emblem shows

Round is the default for finished work; the white box reads as a sticker over
hand-painted art, and looks worst on night scenes.

The mask is geometric rather than a colour key on purpose. The logo's own
lettering and the bull's muzzle are near-white, so keying by colour punches holes
straight through them.

Usage:
    python make_watermark.py --width 96 [--out DIR] [--logo FILE]
"""

import argparse
import os

from PIL import Image, ImageDraw, ImageFilter

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOGO = os.path.join(SKILL, "assets", "logo.jpeg")
SS = 4          # supersample, so the mask edge downsamples smoothly


def content_bbox(img, tol=248):
    grey = img.convert("L")
    return grey.point(lambda v: 255 if v < tol else 0).getbbox()


def squareify(box, size):
    l, t, r, b = box
    side = max(r - l, b - t)
    cx, cy = (l + r) / 2.0, (t + b) / 2.0
    l = max(0, min(int(round(cx - side / 2.0)), size[0] - side))
    t = max(0, min(int(round(cy - side / 2.0)), size[1] - side))
    return (l, t, l + side, t + side)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=96)
    ap.add_argument("--out", default=".")
    ap.add_argument("--logo", default=DEFAULT_LOGO)
    a = ap.parse_args()

    src = Image.open(a.logo).convert("RGB")
    crop = src.crop(squareify(content_bbox(src), src.size))
    os.makedirs(a.out, exist_ok=True)
    w = a.width

    crop.resize((w, w), Image.LANCZOS).save(
        os.path.join(a.out, "watermark_box_%d.png" % w))

    big = crop.resize((w * SS, w * SS), Image.LANCZOS)
    mask = Image.new("L", (w * SS, w * SS), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, w * SS - 1, w * SS - 1), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(SS * 0.4))
    rnd = big.convert("RGBA")
    rnd.putalpha(mask)
    rnd.resize((w, w), Image.LANCZOS).save(
        os.path.join(a.out, "watermark_round_%d.png" % w))
    print("wrote watermark_{box,round}_%d.png in %s" % (w, a.out))


if __name__ == "__main__":
    main()
