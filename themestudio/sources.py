"""Discovering the available options and computing palettes / thumbnails."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from . import colors
from .model import PALETTE_KEYS, Theme

HOME = Path.home()
CACHE_DIR = HOME / ".cache/themestudio"
THUMBS_DIR = CACHE_DIR / "thumbs"
PICKER_DIR = CACHE_DIR / "picker"

WALLPAPER_DIRS = [HOME / "Pictures/wallpapers", HOME / "wallpapers"]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

WAYBAR_STYLES_DIR = HOME / ".config/waybar/style"
KITTY_THEMES_DIR = HOME / ".config/kitty/kitty-themes"
ROFI_THEMES_DIR = HOME / ".config/rofi/themes"
WALLUST_TOML = HOME / ".config/wallust/wallust.toml"

THEME_DIRS = [Path("/usr/share/themes"), HOME / ".themes", HOME / ".local/share/themes"]
ICON_DIRS = [Path("/usr/share/icons"), HOME / ".icons", HOME / ".local/share/icons"]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _names(directory: Path, suffix: str) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted((p.name for p in directory.iterdir() if p.name.endswith(suffix)), key=str.lower)


def waybar_styles() -> list[str]:
    return _names(WAYBAR_STYLES_DIR, ".css")


def kitty_themes() -> list[str]:
    return [n[: -len(".conf")] for n in _names(KITTY_THEMES_DIR, ".conf")]


def rofi_themes() -> list[str]:
    return _names(ROFI_THEMES_DIR, ".rasi")


@lru_cache(maxsize=1)
def wallust_presets() -> list[str]:
    try:
        out = subprocess.run(["wallust", "theme", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in _ANSI_RE.sub("", out).splitlines():
        line = line.strip()
        if line.startswith("- "):
            names.append(line[2:].strip())
    return [n for n in names if n not in ("random", "list")]


def gtk_themes() -> list[str]:
    found = set()
    for d in THEME_DIRS:
        if d.is_dir():
            for p in d.iterdir():
                if any((p / sub).is_dir() for sub in ("gtk-3.0", "gtk-4.0")):
                    found.add(p.name)
    found |= {"Adwaita", "Adwaita-dark"}  # built into GTK
    found.discard("Default")
    return sorted(found, key=str.lower)


def _icon_dirs() -> list[Path]:
    out = []
    for d in ICON_DIRS:
        if d.is_dir():
            out += [p for p in d.iterdir() if p.is_dir()]
    return out


def icon_themes() -> list[str]:
    found = set()
    for p in _icon_dirs():
        if (p / "index.theme").exists() and any(c.is_dir() and c.name not in ("cursors", "hyprcursors") for c in p.iterdir()):
            found.add(p.name)
    found -= {"default", "hicolor", "locolor"}
    return sorted(found, key=str.lower)


def cursor_themes() -> list[str]:
    found = {p.name for p in _icon_dirs() if (p / "cursors").is_dir() or (p / "hyprcursors").is_dir()}
    found.discard("default")
    return sorted(found, key=str.lower)


def wallpapers() -> list[Path]:
    seen, out = set(), []
    for d in WALLPAPER_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*"), key=lambda p: str(p).lower()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS and len(p.relative_to(d).parts) <= 2:
                real = p.resolve()
                if real not in seen:
                    seen.add(real)
                    out.append(p)
    return out


# ---------------------------------------------------------------- palettes

_TEMPLATE = (
    '{"special":{"background":"{{background}}","foreground":"{{foreground}}","cursor":"{{cursor}}"},"colors":{'
    + ",".join(f'"color{i}":"{{{{color{i}}}}}"' for i in range(16))
    + "}}"
)


def _wallust_settings() -> str:
    """Top-level settings (backend, palette, ...) from the user's wallust.toml, minus templates."""
    lines = []
    if WALLUST_TOML.exists():
        for line in WALLUST_TOML.read_text(errors="replace").splitlines():
            if line.strip().startswith("["):
                break
            lines.append(line)
    return "\n".join(lines)


_palette_cache: dict[tuple, dict[str, str] | None] = {}
_palette_lock = threading.Lock()


def _run_wallust_isolated(args: list[str]) -> dict[str, str] | None:
    """Run wallust against a throwaway config so the live desktop files are untouched."""
    with tempfile.TemporaryDirectory(prefix="themestudio-") as tmp:
        tmp = Path(tmp)
        (tmp / "templates").mkdir()
        (tmp / "templates/palette.json").write_text(_TEMPLATE)
        out = tmp / "out.json"
        (tmp / "wallust.toml").write_text(
            _wallust_settings()
            + f"\n[templates]\npalette.template = 'palette.json'\npalette.target = '{out}'\n"
        )
        try:
            subprocess.run(
                ["wallust", args[0], "-s", "-q", "--no-hooks", "-d", str(tmp), *args[1:]],
                capture_output=True,
                timeout=60,
            )
            data = json.loads(out.read_text())
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    flat = {**data["special"], **data["colors"]}
    return {k: flat[k].upper() for k in PALETTE_KEYS}


def preset_palette(name: str) -> dict[str, str] | None:
    return _cached(("preset", name), lambda: _run_wallust_isolated(["theme", "--", name]))


def wallpaper_palette(path: str) -> dict[str, str] | None:
    p = Path(path)
    if not p.is_file():
        return None
    key = ("wallpaper", str(p), p.stat().st_mtime)
    return _cached(key, lambda: _run_wallust_isolated(["run", str(p)]))


def _cached(key, compute):
    with _palette_lock:
        if key in _palette_cache:
            return _palette_cache[key]
    value = compute()
    with _palette_lock:
        _palette_cache[key] = value
    return value


def theme_palette(theme: Theme) -> dict[str, str] | None:
    """The palette the theme would apply, colour adjustments included (blocking; call from a worker thread)."""
    palette = source_palette(theme)
    return palette and colors.adjust(palette, colors.Modifiers.from_theme(theme))


def source_palette(theme: Theme) -> dict[str, str] | None:
    """The theme's colours straight from its source, before any adjustments."""
    src = theme.color_source
    if src == "custom":
        return theme.custom_palette
    if src == "preset" and theme.get("WALLUST_THEME"):
        return preset_palette(theme.get("WALLUST_THEME"))
    if src == "wallpaper" and theme.get("WALLPAPER"):
        return wallpaper_palette(theme.get("WALLPAPER"))
    return None


# ---------------------------------------------------------------- thumbnails


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")[:6]
    try:
        return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0.5, 0.5, 0.5)


def thumbnail(path: str | Path, width: int = 320) -> Path | None:
    """Cached, downscaled PNG of an image (blocking)."""
    from gi.repository import GdkPixbuf, GLib

    p = Path(path)
    if not p.is_file():
        return None
    st = p.stat()
    key = hashlib.sha1(f"{p.resolve()}:{st.st_mtime}:{st.st_size}:{width}".encode()).hexdigest()
    out = THUMBS_DIR / f"{key}.png"
    if out.exists():
        return out
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), width, -1, True)
        pix.savev(str(out), "png", [], [])
    except GLib.Error:
        return None
    return out


def write_picker_thumb(theme: Theme, palette: dict[str, str] | None) -> None:
    """Square icon used by the Super+T rofi picker: wallpaper crop, or palette stripes."""
    from gi.repository import GdkPixbuf, GLib

    size = 256
    PICKER_DIR.mkdir(parents=True, exist_ok=True)
    out = PICKER_DIR / f"{theme.slug}.png"
    wp = theme.get("WALLPAPER")
    try:
        if wp and Path(wp).is_file():
            src = GdkPixbuf.Pixbuf.new_from_file(wp)
            w, h = src.get_width(), src.get_height()
            side = min(w, h)
            crop = src.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
            pix = crop.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        elif palette:
            pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
            pix.fill(_rgba(palette["background"]))
            stripe = size // 8
            for i, key in enumerate(f"color{n}" for n in range(1, 9)):
                sub = pix.new_subpixbuf(i * stripe, size // 2, stripe, size // 2)
                sub.fill(_rgba(palette[key if key != "color8" else "foreground"]))
        else:
            out.unlink(missing_ok=True)
            return
        pix.savev(str(out), "png", [], [])
    except GLib.Error:
        pass


def _rgba(hex_color: str) -> int:
    r, g, b = (round(c * 255) for c in hex_to_rgb(hex_color))
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def remove_picker_thumb(slug: str) -> None:
    (PICKER_DIR / f"{slug}.png").unlink(missing_ok=True)
