"""Code-drawn storyboard cards for stock-free Shorts scenes.

This first prototype creates a whiteboard-style scene card from narration keywords.
It intentionally uses simple vector-like doodles rather than stock footage.
"""
import os
import random
import textwrap
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
INK = (35, 35, 35)
PAPER = (250, 248, 240)


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _line(draw, points, width=9):
    # Slightly imperfect doubled strokes suggest a hand-drawn marker.
    draw.line(points, fill=INK, width=width, joint="curve")
    jittered = [(x + random.randint(-2, 2), y + random.randint(-2, 2))
                for x, y in points]
    draw.line(jittered, fill=INK, width=max(2, width // 3), joint="curve")


def _doodle(draw, kind, cx, cy):
    if kind == "clock":
        draw.ellipse((cx-190, cy-190, cx+190, cy+190), outline=INK, width=10)
        _line(draw, [(cx, cy), (cx, cy-115)], 12)
        _line(draw, [(cx, cy), (cx+95, cy+45)], 12)
        draw.ellipse((cx-14, cy-14, cx+14, cy+14), fill=INK)
    elif kind == "book":
        _line(draw, [(cx,cy-145),(cx,cy+145),(cx-205,cy+105),(cx-205,cy-180),(cx,cy-145)], 10)
        _line(draw, [(cx,cy-145),(cx+205,cy-180),(cx+205,cy+105),(cx,cy+145)], 10)
        for y in (cy-75, cy-15, cy+45):
            _line(draw, [(cx-160,y),(cx-45,y+8)], 5)
            _line(draw, [(cx+45,y+8),(cx+160,y)], 5)
    elif kind == "checklist":
        draw.rounded_rectangle((cx-155,cy-210,cx+155,cy+220), radius=20, outline=INK, width=10)
        for y in (cy-115, cy, cy+115):
            draw.rectangle((cx-95,y-25,cx-45,y+25), outline=INK, width=7)
            _line(draw, [(cx-88,y),(cx-68,y+18),(cx-42,y-20)], 7)
            _line(draw, [(cx-15,y),(cx+95,y)], 7)
    elif kind == "person":
        draw.ellipse((cx-75,cy-225,cx+75,cy-75), outline=INK, width=10)
        _line(draw, [(cx,cy-75),(cx,cy+135),(cx-125,cy+250)], 12)
        _line(draw, [(cx,cy+135),(cx+130,cy+250)], 12)
        _line(draw, [(cx,cy),(cx-145,cy+80)], 12)
        _line(draw, [(cx,cy),(cx+145,cy+75)], 12)
    elif kind == "idea":
        draw.ellipse((cx-135,cy-190,cx+135,cy+80), outline=INK, width=10)
        _line(draw, [(cx-80,cy+65),(cx-65,cy+150),(cx+65,cy+150),(cx+80,cy+65)], 9)
        _line(draw, [(cx-55,cy+190),(cx+55,cy+190)], 9)
        for x,y in ((0,-260),(-220,-120),(220,-120),(-220,100),(220,100)):
            _line(draw, [(cx+x//2,cy+y//2),(cx+x,cy+y)], 7)
    else:
        # Generic hand-drawn speech card.
        draw.rounded_rectangle((cx-220,cy-145,cx+220,cy+145), radius=35, outline=INK, width=10)
        _line(draw, [(cx-80,cy+145),(cx-145,cy+205),(cx-135,cy+135)], 9)
        for y in (cy-45,cy+25):
            _line(draw, [(cx-125,y),(cx+125,y)], 7)


def scene_kind(text):
    t = text.lower()
    if any(w in t for w in ("time", "clock", "minute", "tonight", "alarm")):
        return "clock"
    if any(w in t for w in ("book", "read", "study", "page", "learn")):
        return "book"
    if any(w in t for w in ("plan", "task", "list", "check", "tomorrow")):
        return "checklist"
    if any(w in t for w in ("think", "idea", "focus", "remember")):
        return "idea"
    if any(w in t for w in ("person", "start", "do", "work", "you")):
        return "person"
    return "speech"


def render_scene(text, output_path, index=0):
    random.seed(index)
    image = Image.new("RGB", (W,H), PAPER)
    d = ImageDraw.Draw(image)
    # A loose frame gives the composition a sketched storyboard-panel feel.
    d.rounded_rectangle((55,70,W-55,H-70), radius=42, outline=INK, width=5)
    d.text((105,125), f"SCENE {index+1:02d}", font=_font(38, True), fill=INK)
    _doodle(d, scene_kind(text), W//2, 780)
    wrapped = textwrap.fill(text, width=30)
    d.multiline_text((120,1280), wrapped, font=_font(54, True), fill=INK,
                     spacing=22, align="center", stroke_width=0)
    # Hand-drawn underline.
    _line(d, [(180,1660),(W-180,1652)], 7)
    image.save(output_path)
    return output_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("output")
    parser.add_argument("--index", type=int, default=0)
    args = parser.parse_args()
    render_scene(args.text, args.output, args.index)
