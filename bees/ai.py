"""AI listing writer and smart search.

``write_listing`` turns a product name (plus optional key details) into a
ready-to-use description. With ANTHROPIC_API_KEY set it asks Claude;
otherwise - or if the AI service can't be reached - a built-in writer
produces a clean description from the same details, so the button always
works. Neither writer invents specifications: anything specific comes from
the details the seller typed.

``parse_search`` understands everyday shopping searches such as
"red hoodie under 40" or "lamp between 20 and 50" and turns them into
words + a price range.
"""
import json
import logging
import random
import re
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_NAME = 160
MAX_NOTES = 600

# ---------------------------------------------------------------------------
# Listing writer
# ---------------------------------------------------------------------------

GROUPS = {
    "beauty": ("skincare", "haircare", "lotion-cream"),
    "fashion": ("fashion", "hoodies", "sneakers"),
    "tech": ("electronics", "microphones", "sim-devices", "screen-protector", "3d-printers"),
    "home": ("pasta-tools", "casserole-pot", "table-lamp"),
    "kids": ("toy-boxes", "education", "dress-up-kits", "coloring-drawing"),
    "food": ("grocery",),
    "pets": ("leashes",),
    "giving": ("donate-education",),
}

OPENERS = {
    "beauty": ["Treat yourself to {name}, an easy addition to your daily routine.",
               "Meet {name}: simple, everyday care that fits right into your routine."],
    "fashion": ["Add {name} to your wardrobe for comfortable, easy everyday style.",
                "{name} pairs effortlessly with what you already own and feels great all day."],
    "tech": ["{name} makes everyday tech simpler and more reliable.",
             "Upgrade your setup with {name}, built for everyday use."],
    "home": ["Make time at home easier with {name}.",
             "{name} brings practical, everyday quality to your home."],
    "kids": ["{name} keeps little ones busy, curious and having fun.",
             "Spark creativity and play with {name}."],
    "food": ["Stock your kitchen with {name}, a pantry favourite.",
             "{name} is an everyday essential for your kitchen."],
    "pets": ["Make walks safer and more comfortable with {name}.",
             "{name} is made for everyday adventures with your pet."],
    "giving": ["Support a learner with {name}.",
               "{name} helps put the essentials into the hands of students who need them."],
    "other": ["Discover {name}, chosen for everyday quality and value.",
              "{name}: a dependable choice you'll reach for again and again."],
}

BENEFITS = {
    "beauty": ["Easy to use every day", "Suits a simple, no-fuss routine", "Thoughtful gift for someone special"],
    "fashion": ["Comfortable fit for all-day wear", "Easy to style for casual or smart looks", "A versatile piece you'll wear often"],
    "tech": ["Simple to set up and use", "Compact and easy to carry", "Great for home, work or travel"],
    "home": ["Practical design for everyday use", "Easy to clean and store", "Makes a useful housewarming gift"],
    "kids": ["Encourages imagination and learning", "Great for playtime at home or on the go", "A fun gift for birthdays and holidays"],
    "food": ["Handy pantry staple", "Great for everyday cooking", "Easy to store"],
    "pets": ["Comfortable for daily walks", "Easy to put on and take off", "Made for everyday use"],
    "giving": ["Goes directly to supporting education", "A meaningful way to give back", "Every contribution makes a difference"],
    "other": ["Everyday quality you can rely on", "Great value for money", "Makes a thoughtful gift"],
}

CLOSERS = {
    "beauty": "For best results, follow the directions on the pack.",
    "fashion": "Check the size details before ordering for the perfect fit.",
    "tech": "Check compatibility with your devices before ordering.",
    "home": "A practical upgrade for any home.",
    "kids": "Adult supervision recommended for younger children.",
    "food": "Store in a cool, dry place.",
    "pets": "Choose the right size for your pet for a comfortable fit.",
    "giving": "Thank you for supporting education.",
    "other": "Order today and we'll get it on its way to you.",
}


def _group(category):
    for group, cats in GROUPS.items():
        if category in cats:
            return group
    return "other"


def _details(notes):
    """Splits the seller's notes into short feature lines."""
    parts = re.split(r"[\n,;•]+|\s-\s", notes or "")
    out = []
    for part in parts:
        line = part.strip(" .-*\t")
        if 2 <= len(line) <= 120:
            line = line[0].upper() + line[1:]
            if line.lower() not in (x.lower() for x in out):
                out.append(line)
    return out[:6]


def builtin_listing(name, category="", notes="", category_label=""):
    """Description written from templates. Deterministic per product name."""
    name = (name or "").strip()[:MAX_NAME] or "This product"
    group = _group(category)
    rng = random.Random(name.lower())
    opener = rng.choice(OPENERS[group]).format(name=name)
    details = _details(notes)
    bullets = details[:5]
    target = max(3, min(len(bullets) + 1, 5))
    for benefit in BENEFITS[group]:
        if len(bullets) >= target:
            break
        bullets.append(benefit)
    lines = [opener, "", "Why you'll love it:"] + [f"• {b}" for b in bullets] + ["", CLOSERS[group]]
    return "\n".join(lines)


PROMPT = """Write a product description for an online marketplace listing.

Product name: {name}
Category: {category}
Details from the seller: {notes}

Rules:
- Plain text only, no markdown, no headings with #, no emojis.
- Format: one or two short opening sentences, a blank line, the line "Why you'll love it:", then 3 to 5 lines starting with "• ", then a blank line and one short closing sentence.
- Under 120 words. Friendly, clear, persuasive; written for international shoppers in simple English.
- Only state facts given in the name or the seller's details. Never invent sizes, materials, certifications, warranties, ingredients or health claims.
- Reply with the description only."""


def _ai_listing(name, category_label, notes):
    body = json.dumps({
        "model": settings.AI_MODEL,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": PROMPT.format(
            name=name, category=category_label or "General", notes=notes or "(none)")}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": settings.AI_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()
    text = re.sub(r"[*#`]+", "", text).strip()
    if len(text) < 40:
        raise ValueError("AI reply too short")
    return text[:2000]


def write_listing(name, category="", notes="", category_label=""):
    """Returns (description, source) where source is "ai" or "builtin"."""
    name = (name or "").strip()[:MAX_NAME]
    notes = (notes or "").strip()[:MAX_NOTES]
    if getattr(settings, "AI_API_KEY", ""):
        try:
            return _ai_listing(name, category_label, notes), "ai"
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("AI listing writer unavailable, using built-in writer: %s", exc)
    return builtin_listing(name, category, notes, category_label), "builtin"


# ---------------------------------------------------------------------------
# Smart search
# ---------------------------------------------------------------------------

_NUM = r"\$?\s?(\d+(?:[.,]\d{1,2})?)\s?(?:\$|usd|dollars?|eur|euros?|gbp|pounds?|rs\.?|pkr|aed)?"
PRICE_PATTERNS = [
    (re.compile(r"\bbetween\s+" + _NUM + r"\s+(?:and|to|-)\s+" + _NUM, re.I), "range"),
    (re.compile(r"(?<![\w.])" + r"\$?\s?(\d+(?:[.,]\d{1,2})?)\s?(?:-|to)\s?\$?\s?(\d+(?:[.,]\d{1,2})?)\s?(?:\$|usd|dollars?)?(?![\w.])", re.I), "range"),
    (re.compile(r"\b(?:under|below|less than|cheaper than|max(?:imum)?|up to|upto|within)\s+" + _NUM, re.I), "max"),
    (re.compile(r"<\s*=?\s*" + _NUM, re.I), "max"),
    (re.compile(r"\b(?:over|above|more than|from|min(?:imum)?|at least)\s+" + _NUM, re.I), "min"),
    (re.compile(r">\s*=?\s*" + _NUM, re.I), "min"),
]
STOP_WORDS = {"a", "an", "the", "for", "with", "and", "or", "of", "in", "on", "to", "me", "my", "show", "find",
              "buy", "best", "good", "cheap", "price", "priced", "items", "item", "products", "product", "some"}


def _num(text):
    return float(text.replace(",", "."))


def parse_search(query):
    """'red hoodie under 40' -> {"words": ["red", "hoodie"], "min": None,
    "max": 40.0, "cheap": False}."""
    text = (query or "").strip()
    result = {"words": [], "min": None, "max": None, "cheap": False}
    for pattern, kind in PRICE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if kind == "range":
            lo, hi = sorted((_num(m.group(1)), _num(m.group(2))))
            if result["min"] is None and result["max"] is None:
                result["min"], result["max"] = lo, hi
        elif kind == "max" and result["max"] is None:
            result["max"] = _num(m.group(1))
        elif kind == "min" and result["min"] is None:
            result["min"] = _num(m.group(1))
        text = text[:m.start()] + " " + text[m.end():]
    lowered = text.lower()
    result["cheap"] = bool(re.search(r"\b(cheap|cheapest|budget|affordable|low price)\b", lowered))
    words = re.findall(r"[^\W_]+(?:['-][^\W_]+)*", lowered)
    result["words"] = [w for w in words if w not in STOP_WORDS and not w.isdigit()][:8]
    return result


def singular(word):
    """Very small stemmer so 'hoodies' finds 'hoodie' and 'lamps' finds 'lamp'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
