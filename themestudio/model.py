"""Theme bundle files: ~/.config/hypr/themes/*.theme (bash-sourceable KEY="value")."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

THEMES_DIR = Path.home() / ".config/hypr/themes"

# Field order used when writing files
FIELDS = [
    "NAME",
    "WALLPAPER",
    "COLOR_SOURCE",
    "WALLUST_THEME",
    "CUSTOM_COLORS",
    "COLOR_HUES",
    "COLOR_TINT",
    "COLOR_VIBRANCY",
    "COLOR_BRIGHTNESS",
    "COLOR_CONTRAST",
    "COLOR_OVERRIDES",
    "WAYBAR_STYLE",
    "KITTY_THEME",
    "ROFI_THEME",
    "GTK_THEME",
    "ICON_THEME",
    "CURSOR_THEME",
    "COLOR_SCHEME",
]

COLOR_SOURCES = ["wallpaper", "preset", "custom", "none"]

# background, foreground, cursor, color0..color15
PALETTE_KEYS = ["background", "foreground", "cursor"] + [f"color{i}" for i in range(16)]

_LINE_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)=(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        out, i = [], 1
        while i < len(raw) and raw[i] != '"':
            if raw[i] == "\\" and i + 1 < len(raw):
                i += 1
            out.append(raw[i])
            i += 1
        return "".join(out)
    if raw.startswith("'"):
        end = raw.find("'", 1)
        return raw[1:end] if end != -1 else raw[1:]
    return raw.split("#", 1)[0].strip()


def _quote(value: str) -> str:
    return '"' + re.sub(r'([\\"$`])', r"\\\1", value) + '"'


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return slug or "Theme"


@dataclass
class Theme:
    values: dict[str, str] = field(default_factory=dict)
    path: Path | None = None
    # Lines we don't understand (comments, other keys) are kept on save
    extra_lines: list[str] = field(default_factory=list)

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    @property
    def name(self) -> str:
        return self.get("NAME") or (self.path.stem if self.path else "Untitled")

    @property
    def slug(self) -> str:
        return self.path.stem if self.path else slugify(self.name)

    @property
    def color_source(self) -> str:
        src = self.get("COLOR_SOURCE")
        if src in COLOR_SOURCES:
            return src
        if self.get("CUSTOM_COLORS"):
            return "custom"
        if self.get("WALLUST_THEME"):
            return "preset"
        if self.get("WALLPAPER"):
            return "wallpaper"
        return "none"

    @property
    def custom_palette(self) -> dict[str, str] | None:
        parts = self.get("CUSTOM_COLORS").split()
        if len(parts) != len(PALETTE_KEYS):
            return None
        return dict(zip(PALETTE_KEYS, parts))

    def set_custom_palette(self, palette: dict[str, str] | None) -> None:
        self.set("CUSTOM_COLORS", " ".join(palette[k] for k in PALETTE_KEYS) if palette else "")

    def copy(self) -> "Theme":
        return Theme(dict(self.values), self.path, list(self.extra_lines))

    def serialize(self) -> str:
        lines = [f"{k}={_quote(self.get(k))}" for k in FIELDS]
        lines += [f"{k}={_quote(v)}" for k, v in self.values.items() if k not in FIELDS]
        lines += self.extra_lines
        return "\n".join(lines) + "\n"


def load(path: Path) -> Theme:
    theme = Theme(path=path)
    for line in path.read_text(errors="replace").splitlines():
        m = _LINE_RE.match(line)
        if m:
            theme.values[m.group(1)] = _unquote(m.group(2))
        elif line.strip():
            theme.extra_lines.append(line)
    return theme


def load_all() -> list[Theme]:
    if not THEMES_DIR.is_dir():
        return []
    themes = [load(p) for p in sorted(THEMES_DIR.glob("*.theme"))]
    return sorted(themes, key=lambda t: t.name.lower())


def unique_path(name: str, current: Path | None = None) -> Path:
    base = slugify(name)
    candidate = THEMES_DIR / f"{base}.theme"
    n = 2
    while candidate.exists() and candidate != current:
        candidate = THEMES_DIR / f"{base}-{n}.theme"
        n += 1
    return candidate


def save(theme: Theme) -> Path:
    """Write the theme; renames the file when the display name changed."""
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    wanted = unique_path(theme.name, theme.path)
    if theme.path and theme.path.exists() and theme.path != wanted:
        theme.path.rename(wanted)
    theme.path = wanted
    tmp = wanted.with_suffix(".theme.tmp")
    tmp.write_text(theme.serialize())
    tmp.replace(wanted)
    return wanted


def delete(theme: Theme) -> None:
    if theme.path and theme.path.exists():
        theme.path.unlink()
