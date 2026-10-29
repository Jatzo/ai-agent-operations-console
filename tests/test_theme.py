import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "web" / "app.css").read_text(encoding="utf-8")


def tokens():
    root = re.search(r":root\{(.*?)\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", root))


def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Pairs the page really draws: text on a panel, coloured text on a panel, dark text on a filled control.
TEXT_PAIRS = [
    ("text", "bg"), ("text", "panel"),
    ("quiet", "bg"), ("quiet", "panel"),
    ("accent", "bg"), ("accent", "panel"),
    ("green", "bg"), ("green", "panel"), ("violet", "panel"), ("amber", "panel"), ("pale", "bg"), ("ink", "bg"),
    ("alert", "bg"), ("alert", "panel"),
    ("bg", "accent"),
]


def test_every_text_and_background_pair_the_page_uses_is_readable():
    palette = tokens()
    too_low = {f"{front} on {back}": round(contrast(palette[front], palette[back]), 2) for front, back in TEXT_PAIRS if contrast(palette[front], palette[back]) < 4.5}

    assert too_low == {}


def test_the_palette_defines_every_colour_the_pairs_name():
    palette = tokens()

    assert {name for pair in TEXT_PAIRS for name in pair} <= set(palette)


def test_the_stylesheet_has_no_external_address_and_no_data_image():
    assert "url(" not in CSS
    assert "@import" not in CSS
