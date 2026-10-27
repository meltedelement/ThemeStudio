"""Colour adjustments applied on top of a theme's colour source (wallpaper, preset or custom).

wallust picks wallpaper colours by prominence in the image, which causes two problems:
  - on a muted wallpaper every slot comes out the same hue, so "red", "green" and "blue" are all
    brown. "Fix terminal colours" turns each ANSI slot towards its own hue, leaning a little
    towards the wallpaper ("tint").
  - on a dark wallpaper colours land almost on top of the background. The contrast rule pushes
    each colour's lightness away from the background (keeping its hue) until it is readable.

On top of that each theme can set vibrancy, brightness and pin specific colours. The settings
live in the .theme file (COLOR_HUES, COLOR_TINT, ...); blank means the default for the source.

Every palette goes through adjust() before wallust renders any template, so Kitty, Ghostty,
Waybar, Rofi, Hyprland etc. all get the same colours, and the studio preview matches.

CLI: python3 -m themestudio.colors <file.theme> <scheme-out.json>
     writes the theme's final palette as a pywal scheme for `wallust cs -f pywal`
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .model import PALETTE_KEYS, Theme

# OKLCH hue (degrees) each ANSI slot should read as; color9-14 are the bright versions
ANSI_HUES = {1: 29, 2: 142, 3: 95, 4: 264, 5: 328, 6: 195}  # red green yellow blue magenta cyan
MIN_CHROMA = 0.09  # enough colourfulness to tell the hues apart; muted, not neon
BRIGHT_STEP = 0.08  # minimum OKLab lightness gap between a bright colour and its normal version

# Colours drawn as text on the background; color0 is "black", used as a background far more often
TEXT_KEYS = ["foreground", "cursor"] + [f"color{i}" for i in range(1, 16)]


@dataclass
class Modifiers:
    hues: bool = False  # fix terminal colours
    tint: float = 15  # degrees a fixed hue may lean towards the original colour
    vibrancy: float = 100  # percent of the original colourfulness
    brightness: float = 0  # -50..50, shifts text colours lighter / darker
    contrast: float = 0  # minimum WCAG contrast for text colours; 0 = off
    overrides: dict[str, str] = field(default_factory=dict)  # pinned colours, applied last

    @staticmethod
    def defaults(source: str) -> "Modifiers":
        """Wallpaper palettes get fixed up by default; presets and custom palettes are left as made."""
        return Modifiers(hues=True, contrast=4.5) if source == "wallpaper" else Modifiers()

    @staticmethod
    def from_theme(theme: Theme) -> "Modifiers":
        m = Modifiers.defaults(theme.color_source)
        if theme.get("COLOR_HUES") in ("on", "off"):
            m.hues = theme.get("COLOR_HUES") == "on"
        for attr, key in (("tint", "COLOR_TINT"), ("vibrancy", "COLOR_VIBRANCY"),
                          ("brightness", "COLOR_BRIGHTNESS"), ("contrast", "COLOR_CONTRAST")):
            try:
                setattr(m, attr, float(theme.get(key)))
            except ValueError:
                pass  # blank or invalid: keep the default
        m.overrides = parse_overrides(theme.get("COLOR_OVERRIDES"))
        return m


def parse_overrides(value: str) -> dict[str, str]:
    """'color4=#7AA2F7 background=#101018' -> {'color4': '#7AA2F7', 'background': '#101018'}"""
    out = {}
    for part in value.split():
        key, _, color = part.partition("=")
        if key in PALETTE_KEYS and len(color) == 7 and color.startswith("#"):
            out[key] = color.upper()
    return out


def format_overrides(overrides: dict[str, str]) -> str:
    return " ".join(f"{k}={overrides[k]}" for k in PALETTE_KEYS if k in overrides)


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")[:6]
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02X}" for c in rgb)


def luminance(hex_color: str) -> float:
    r, g, b = (_to_linear(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# OKLab/OKLCH, so lightness changes keep the hue looking the same
def _oklch(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (_to_linear(c) for c in _rgb(hex_color))
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(A, B), math.atan2(B, A)


def _from_oklch(L: float, C: float, H: float) -> tuple[float, float, float] | None:
    A, B = C * math.cos(H), C * math.sin(H)
    l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
    m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
    s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
    rgb = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960771 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    if any(c < -1e-4 or c > 1 + 1e-4 for c in rgb):
        return None
    return tuple(_to_srgb(max(0.0, c)) for c in rgb)  # type: ignore[return-value]


def _at_lightness(L: float, C: float, H: float) -> str:
    """Colour at lightness L with the given hue, dropping chroma until it fits in sRGB."""
    while C > 1e-3:
        rgb = _from_oklch(L, C, H)
        if rgb:
            return _hex(rgb)
        C *= 0.9
    return _hex(_from_oklch(L, 0, H) or (L, L, L))


def ensure_contrast(color: str, background: str, minimum: float) -> str:
    """Move `color` away from `background` in lightness until it reaches `minimum` contrast."""
    if contrast(color, background) >= minimum:
        return color
    L, C, H = _oklch(color)
    lighten = contrast("#FFFFFF", background) >= contrast("#000000", background)
    lo, hi = (L, 1.0) if lighten else (0.0, L)
    for _ in range(24):  # binary search for the smallest change that passes
        mid = (lo + hi) / 2
        ok = contrast(_at_lightness(mid, C, H), background) >= minimum
        if lighten:
            lo, hi = (lo, mid) if ok else (mid, hi)
        else:
            lo, hi = (mid, hi) if ok else (lo, mid)
    return _at_lightness(hi if lighten else lo, C, H)


def contrast_rules(minimum: float) -> dict[str, float]:
    """Per-colour minimums from the theme's setting (4.5 gives WCAG body text: 7 / 4.5 / 3)."""
    rules = {k: minimum for k in TEXT_KEYS}
    rules["foreground"] = min(21.0, minimum * 14 / 9)
    # Cursor and "bright black" (comments, autosuggestions) are meant to be dim but visible
    rules["cursor"] = rules["color8"] = minimum * 2 / 3
    return rules


def ansi_hue(color: str, slot: int, tint: float) -> str:
    """Turn `color` to the hue of ANSI `slot`, keeping its lightness and leaning up to `tint` degrees towards it."""
    L, C, H = _oklch(color)
    target = math.radians(ANSI_HUES[slot])
    lean = math.radians(tint)
    # Greys have no real hue to lean towards
    drift = (H - target + math.pi) % (2 * math.pi) - math.pi if C > 0.02 else 0.0
    return _at_lightness(L, max(C, MIN_CHROMA), target + max(-lean, min(lean, drift)))


def adjust(palette: dict[str, str], mods: Modifiers) -> dict[str, str]:
    """`palette` with the theme's modifiers applied, in order: hues, vibrancy, brightness, contrast, pins."""
    out = dict(palette)
    if "background" in mods.overrides:  # everything else is judged against the pinned background
        out["background"] = mods.overrides["background"]
    bg = out["background"]
    dark = contrast("#FFFFFF", bg) >= contrast("#000000", bg)

    if mods.hues:
        for i in range(1, 15):
            if i % 8 in ANSI_HUES:
                out[f"color{i}"] = ansi_hue(out[f"color{i}"], i % 8, mods.tint)

    if mods.vibrancy != 100:
        for k in PALETTE_KEYS:
            if k not in mods.overrides:
                L, C, H = _oklch(out[k])
                out[k] = _at_lightness(L, C * mods.vibrancy / 100, H)

    if mods.brightness:
        for k in TEXT_KEYS:
            L, C, H = _oklch(out[k])
            out[k] = _at_lightness(min(1.0, max(0.0, L + mods.brightness / 200)), C, H)

    if mods.contrast > 0:
        for k, minimum in contrast_rules(mods.contrast).items():
            out[k] = ensure_contrast(out[k], bg, minimum)

    # Keep bright colours (color9-14) visibly brighter than their normal versions on dark backgrounds
    if mods.hues and dark:
        for slot in ANSI_HUES:
            normal_L = _oklch(out[f"color{slot}"])[0]
            L, C, H = _oklch(out[f"color{slot + 8}"])
            if L < normal_L + BRIGHT_STEP:
                out[f"color{slot + 8}"] = _at_lightness(min(1.0, normal_L + BRIGHT_STEP), C, H)

    out.update(mods.overrides)
    return out


def write_pywal_scheme(palette: dict[str, str], path: Path) -> None:
    """Write `palette` as a pywal colorscheme, the format `wallust cs -f pywal` reads."""
    data = {
        "special": {k: palette[k] for k in ("background", "foreground", "cursor")},
        "colors": {f"color{i}": palette[f"color{i}"] for i in range(16)},
    }
    path.write_text(json.dumps(data) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python3 -m themestudio.colors <file.theme> <scheme-out.json>")
    from . import model, sources

    palette = sources.theme_palette(model.load(Path(sys.argv[1])))  # already passed through adjust()
    if not palette:
        sys.exit(f"could not work out the colours for {sys.argv[1]}")
    write_pywal_scheme(palette, Path(sys.argv[2]))
