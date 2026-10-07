"""
Packaging-style product images for the demo catalogue.

Every demo product gets a studio-style render of its own package (tube,
bottle, jar, carton, pouch, garment or box) with the product's name printed
on the label, so the picture always matches the product. Images are drawn
with Pillow, so there is no dependency on stock-photo sites and nothing to
license. Real products added by sellers or staff use their own uploads.
"""
import hashlib
import io
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 1200          # drawing size; downsampled for smooth edges
OUT = 640            # saved size

# (package, ink on package, background top, background bottom)
PALETTES = [
    ("#1F4E5F", "#FFFFFF", "#E7F0F2", "#CFE0E4"),
    ("#F3EDE2", "#2B2A26", "#F7E9D7", "#EBD5B8"),
    ("#2F2F35", "#F2D27A", "#E9E7EE", "#D6D3DE"),
    ("#E86A50", "#FFFFFF", "#FBE7E1", "#F3CFC5"),
    ("#FFFFFF", "#1E3A5F", "#E3ECF7", "#C9D9EE"),
    ("#6B8F71", "#FFFFFF", "#E8F0E6", "#D2E2CE"),
    ("#F2B33D", "#2A2112", "#FBF1DC", "#F2DFB4"),
    ("#7A5BA6", "#FFFFFF", "#EEE8F6", "#DCD1EC"),
    ("#0E3B43", "#F2B33D", "#E4EEEF", "#C8DCDE"),
    ("#D9E6EC", "#18323C", "#EEF3F5", "#D6E3E8"),
]

KIND_RULES = [
    ("tube", ["face wash", "gel", "sunblock", "scrub", "hand and foot", "night cream", "curl defining"]),
    ("bottle", ["serum", "shampoo", "conditioner", "hair growth oil", "lotion", "cooking oil", "moisturizer 100ml"]),
    ("jar", ["butter", "honey", "moisturizer"]),
    ("pouch", ["rice", "lentil", "filament"]),
    ("garment", ["t-shirt", "tunic", "jacket", "hoodie", "sweatshirt", "trouser", "scarf", "costume", "dress-up", "uniform"]),
    ("pot", ["pot", "casserole"]),
]


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def kind_for(name, category=""):
    n = name.lower()
    for kind, words in KIND_RULES:
        if any(w in n for w in words):
            return kind
    if category in ("hoodies", "fashion"):
        return "garment"
    return "box"


def palette_for(name):
    h = int(hashlib.sha1(name.encode()).hexdigest(), 16)
    return PALETTES[h % len(PALETTES)]


def _gradient(top, bottom):
    img = Image.new("RGB", (SIZE, SIZE), top)
    t, b = _hex(top), _hex(bottom)
    draw = ImageDraw.Draw(img)
    for y in range(SIZE):
        k = y / (SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
    return img


def _shadow(img, box, strength=90):
    layer = Image.new("L", img.size, 0)
    ImageDraw.Draw(layer).ellipse(box, fill=strength)
    layer = layer.filter(ImageFilter.GaussianBlur(28))
    dark = Image.new("RGB", img.size, (20, 26, 31))
    img.paste(dark, (0, 0), layer)


def _text_block(draw, center_x, top, width_px, text, ink, max_size=72, min_size=34, max_lines=3, line_gap=10):
    """Wraps and fits the product name inside a label area."""
    size = max_size
    while size >= min_size:
        font = _font(size)
        avg = max(font.getlength("abcdefghij") / 10, 1)
        lines = textwrap.wrap(text, width=max(int(width_px / avg), 4))
        if len(lines) <= max_lines and all(font.getlength(l) <= width_px for l in lines):
            break
        size -= 4
    font = _font(size)
    lines = textwrap.wrap(text, width=max(int(width_px / max(font.getlength("abcdefghij") / 10, 1)), 4))[:max_lines]
    y = top
    for line in lines:
        w = font.getlength(line)
        draw.text((center_x - w / 2, y), line, font=font, fill=ink)
        y += size + line_gap
    return y


def _highlight(img, box, radius):
    """Soft vertical sheen on the left of a package for a 3D feel."""
    x0, y0, x1, y1 = box
    sheen = Image.new("L", img.size, 0)
    w = (x1 - x0)
    ImageDraw.Draw(sheen).rounded_rectangle((x0 + w * 0.12, y0 + 30, x0 + w * 0.22, y1 - 30), radius=radius, fill=70)
    sheen = sheen.filter(ImageFilter.GaussianBlur(18))
    img.paste(Image.new("RGB", img.size, (255, 255, 255)), (0, 0), sheen)


def render(name, category="", subtitle="", variant=0):
    pkg, ink, bg_top, bg_bottom = palette_for(name)
    if variant:
        bg_top, bg_bottom = bg_bottom, bg_top
    img = _gradient(bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)
    kind = kind_for(name, category)
    cx = SIZE // 2
    pkg_rgb, ink_rgb = _hex(pkg), _hex(ink)
    edge = tuple(max(c - 28, 0) for c in pkg_rgb)
    # Light packages get a soft outline so they don't melt into the background.
    outline = tuple(max(c - 45, 0) for c in pkg_rgb) if sum(pkg_rgb) > 600 else None
    sub = (subtitle or category.replace("-", " ")).upper()

    if kind == "tube":
        _shadow(img, (cx - 230, 1010, cx + 230, 1080))
        body = (cx - 210, 210, cx + 210, 930)
        draw.polygon([(cx - 230, 200), (cx + 230, 200), (cx + 210, 260), (cx - 210, 260)], fill=edge)  # crimp
        draw.rounded_rectangle(body, radius=40, fill=pkg_rgb, outline=outline, width=4)
        draw.rounded_rectangle((cx - 120, 930, cx + 120, 1050), radius=24, fill=_hex(ink) if pkg_rgb[0] > 200 else edge)
        _highlight(img, body, 30)
        draw = ImageDraw.Draw(img)
        draw.text((cx - _font(28).getlength(sub) / 2, 360), sub, font=_font(28), fill=ink_rgb)
        _text_block(draw, cx, 430, 330, name, ink_rgb, max_size=64)
    elif kind == "bottle":
        _shadow(img, (cx - 250, 1010, cx + 250, 1085))
        draw.rounded_rectangle((cx - 50, 150, cx + 50, 260), radius=14, fill=edge)        # pump
        draw.rounded_rectangle((cx - 50, 150, cx + 150, 185), radius=12, fill=edge)
        draw.rounded_rectangle((cx - 90, 240, cx + 90, 330), radius=20, fill=edge)        # collar
        body = (cx - 220, 310, cx + 220, 1040)
        draw.rounded_rectangle(body, radius=90, fill=pkg_rgb, outline=outline, width=4)
        _highlight(img, body, 40)
        draw = ImageDraw.Draw(img)
        label_fill = (255, 255, 255) if sum(pkg_rgb) < 600 else _hex("#0E3B43")
        label_ink = _hex("#141A1F") if label_fill == (255, 255, 255) else (255, 255, 255)
        draw.rounded_rectangle((cx - 175, 470, cx + 175, 860), radius=22, fill=label_fill)
        draw.text((cx - _font(26).getlength(sub) / 2, 500), sub, font=_font(26), fill=label_ink)
        _text_block(draw, cx, 560, 300, name, label_ink, max_size=58)
    elif kind == "jar":
        _shadow(img, (cx - 330, 900, cx + 330, 990))
        draw.rounded_rectangle((cx - 320, 360, cx + 320, 480), radius=30, fill=edge)        # lid
        body = (cx - 300, 460, cx + 300, 950)
        draw.rounded_rectangle(body, radius=70, fill=pkg_rgb, outline=outline, width=4)
        _highlight(img, body, 40)
        draw = ImageDraw.Draw(img)
        draw.text((cx - _font(28).getlength(sub) / 2, 560), sub, font=_font(28), fill=ink_rgb)
        _text_block(draw, cx, 620, 480, name, ink_rgb, max_size=66, max_lines=2)
    elif kind == "pouch":
        _shadow(img, (cx - 320, 1000, cx + 320, 1080))
        draw.polygon([(cx - 270, 250), (cx + 270, 250), (cx + 320, 1030), (cx - 320, 1030)], fill=pkg_rgb)
        draw.rectangle((cx - 270, 230, cx + 270, 290), fill=edge)
        for x in range(cx - 260, cx + 260, 24):
            draw.line([(x, 236), (x, 284)], fill=pkg_rgb, width=4)
        draw = ImageDraw.Draw(img)
        draw.ellipse((cx - 210, 420, cx + 210, 840), fill=(255, 255, 255))
        draw.text((cx - _font(26).getlength(sub) / 2, 500), sub, font=_font(26), fill=_hex("#56606B"))
        _text_block(draw, cx, 560, 300, name, _hex("#141A1F"), max_size=60)
    elif kind == "garment":
        _shadow(img, (cx - 380, 1010, cx + 380, 1090), strength=60)
        pts = [(cx - 140, 230), (cx - 60, 270), (cx + 60, 270), (cx + 140, 230), (cx + 400, 360), (cx + 330, 540),
               (cx + 240, 500), (cx + 250, 1020), (cx - 250, 1020), (cx - 240, 500), (cx - 330, 540), (cx - 400, 360)]
        draw.polygon(pts, fill=pkg_rgb)
        draw.arc((cx - 80, 200, cx + 80, 330), 20, 160, fill=edge, width=14)
        draw.line([(cx - 240, 500), (cx - 245, 1020)], fill=edge, width=6)
        draw.line([(cx + 240, 500), (cx + 245, 1020)], fill=edge, width=6)
        # hang tag
        draw.line([(cx + 120, 300), (cx + 210, 640)], fill=_hex("#56606B"), width=4)
        draw.rounded_rectangle((cx + 60, 640, cx + 420, 960), radius=18, fill=(255, 255, 255), outline=_hex("#DDE2E6"), width=3)
        draw.ellipse((cx + 225, 655, cx + 255, 685), fill=_hex("#DDE2E6"))
        draw.text((cx + 240 - _font(22).getlength(sub) / 2, 700), sub, font=_font(22), fill=_hex("#56606B"))
        _text_block(draw, cx + 240, 745, 310, name, _hex("#141A1F"), max_size=44, min_size=28)
    elif kind == "pot":
        _shadow(img, (cx - 380, 880, cx + 380, 970))
        draw.rounded_rectangle((cx - 60, 300, cx + 60, 360), radius=20, fill=edge)        # knob
        draw.ellipse((cx - 330, 340, cx + 330, 450), fill=edge)                            # lid
        draw.rounded_rectangle((cx - 420, 470, cx - 300, 520), radius=20, fill=edge)       # handles
        draw.rounded_rectangle((cx + 300, 470, cx + 420, 520), radius=20, fill=edge)
        body = (cx - 320, 420, cx + 320, 930)
        draw.rounded_rectangle(body, radius=80, fill=pkg_rgb)
        _highlight(img, body, 40)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((cx - 230, 560, cx + 230, 800), radius=18, fill=(255, 255, 255))
        _text_block(draw, cx, 600, 400, name, _hex("#141A1F"), max_size=54, max_lines=2)
    else:  # carton box
        _shadow(img, (cx - 380, 960, cx + 420, 1050))
        front = (cx - 300, 330, cx + 220, 1000)
        side = [(cx + 220, 330), (cx + 330, 250), (cx + 330, 920), (cx + 220, 1000)]
        top = [(cx - 300, 330), (cx - 190, 250), (cx + 330, 250), (cx + 220, 330)]
        draw.polygon(side, fill=edge)
        draw.polygon(top, fill=tuple(min(c + 22, 255) for c in pkg_rgb))
        draw.rectangle(front, fill=pkg_rgb, outline=outline, width=4 if outline else 0)
        draw.rectangle((front[0], front[1] + 70, front[2], front[1] + 86), fill=_hex(ink) if pkg_rgb[0] < 200 else edge)
        draw.text((cx - 40 - _font(28).getlength(sub) / 2, 470), sub, font=_font(28), fill=ink_rgb)
        _text_block(draw, cx - 40, 540, 430, name, ink_rgb, max_size=68)

    return img.resize((OUT, OUT), Image.LANCZOS)


def render_bytes(name, category="", subtitle="", variant=0):
    buf = io.BytesIO()
    render(name, category, subtitle, variant).save(buf, "WEBP", quality=84, method=6)
    return buf.getvalue()
