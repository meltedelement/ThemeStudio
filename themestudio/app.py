"""Theme Studio: create, edit and apply Hyprland theme bundles."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import colors, model, sources  # noqa: E402
from .model import PALETTE_KEYS, Theme  # noqa: E402

APP_ID = "dev.meltedelement.ThemeStudio"
THEME_APPLY = Path(__file__).resolve().parent.parent / "bin/theme-apply"
CURRENT_FILE = sources.CACHE_DIR / "current"

COLOR_SOURCE_LABELS = {
    "wallpaper": "Generate from wallpaper",
    "preset": "Wallust preset",
    "custom": "Custom colours",
    "none": "Leave unchanged",
}
COLOR_SCHEME_LABELS = {"": "Unchanged", "prefer-dark": "Dark", "prefer-light": "Light", "default": "Default"}

# Catppuccin Mocha, used as a starting point for a fresh custom palette
DEFAULT_PALETTE = dict(
    zip(
        PALETTE_KEYS,
        "#1E1E2E #CDD6F4 #F5E0DC #45475A #F38BA8 #A6E3A1 #F9E2AF #89B4FA #F5C2E7 #94E2D5 #BAC2DE "
        "#585B70 #F38BA8 #A6E3A1 #F9E2AF #89B4FA #F5C2E7 #94E2D5 #A6ADC8".split(),
    )
)
NEUTRAL_PALETTE = dict(DEFAULT_PALETTE, background="#2A2A2E", foreground="#E0E0E0", color12="#888888")

# (key, label, column, row) for the 19-colour grids
_ANSI_NAMES = ["Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White"]
PALETTE_GRID = [("background", "Background", 1, 0), ("foreground", "Text", 3, 0), ("cursor", "Cursor", 5, 0)]
PALETTE_GRID += [(f"color{i}", f"{i} {n}", i, 1) for i, n in enumerate(_ANSI_NAMES)]
PALETTE_GRID += [(f"color{i + 8}", f"{i + 8} Bright", i, 2) for i in range(8)]

# Theme keys holding colour adjustments (see colors.Modifiers)
ADJUST_KEYS = ["COLOR_HUES", "COLOR_TINT", "COLOR_VIBRANCY", "COLOR_BRIGHTNESS", "COLOR_CONTRAST", "COLOR_OVERRIDES"]
PALETTE_INPUT_KEYS = {"WALLUST_THEME", "COLOR_SOURCE", "CUSTOM_COLORS", "WALLPAPER", *ADJUST_KEYS}

APP_CSS = """
.ts-thumb { border-radius: 6px; }
.ts-preview { border-radius: 12px; }
.ts-gallery-item { border-radius: 6px; }
flowboxchild:selected .ts-gallery-item { outline: 3px solid @accent_color; outline-offset: -3px; }
.ts-swatch-label { font-size: 8pt; }
.ts-unpin { min-width: 18px; min-height: 18px; padding: 0; }
"""

executor = ThreadPoolExecutor(max_workers=4)


def run_bg(func, callback=None):
    """Run func in a worker thread and hand its result to callback on the main loop."""

    def done(fut):
        if callback is None:
            return
        try:
            result = fut.result()
        except Exception as e:  # noqa: BLE001
            print(f"themestudio: background task failed: {e}", file=sys.stderr)
            return
        GLib.idle_add(lambda: callback(result) and False)

    executor.submit(func).add_done_callback(done)


def rgba(hex_color: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if not c.parse(hex_color):
        c.parse("#808080")
    return c


def rgba_hex(c: Gdk.RGBA) -> str:
    return "#%02X%02X%02X" % tuple(round(v * 255) for v in (c.red, c.green, c.blue))


def current_slug() -> str:
    try:
        return CURRENT_FILE.read_text().strip()
    except OSError:
        return ""


def describe_colors(theme: Theme) -> str:
    src = theme.color_source
    if src == "preset":
        text = f"Preset · {theme.get('WALLUST_THEME')}"
    else:
        text = {"wallpaper": "Wallpaper colours", "custom": "Custom colours"}.get(src, "Colours unchanged")
    if src != "none" and any(theme.get(k) for k in ADJUST_KEYS):
        text += " · adjusted"
    return text


# ---------------------------------------------------------------- widgets


class SizedPicture(Gtk.Picture):
    """A Gtk.Picture whose requested size ignores the image's own dimensions."""

    def __init__(self, width: int, height: int, min_width: int | None = None, **kwargs):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True, **kwargs)
        self._size = (width, height)
        self._min_width = width if min_width is None else min_width
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self._min_width, max(self._min_width, self._size[0]), -1, -1
        return self._size[1], self._size[1], -1, -1


class PaletteStrip(Gtk.DrawingArea):
    """Background block with the eight normal colours as stripes along the bottom."""

    def __init__(self, width: int, height: int):
        super().__init__()
        self.palette: dict[str, str] | None = None
        self.set_size_request(width, height)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("ts-thumb")
        self.set_draw_func(self._draw)

    def set_palette(self, palette):
        self.palette = palette
        self.queue_draw()

    def _draw(self, _area, cr, w, h):
        p = self.palette or NEUTRAL_PALETTE
        cr.set_source_rgb(*sources.hex_to_rgb(p["background"]))
        cr.paint()
        if not self.palette:
            return
        keys = [f"color{i}" for i in range(1, 8)] + ["foreground"]
        sw = w / len(keys)
        for i, k in enumerate(keys):
            cr.set_source_rgb(*sources.hex_to_rgb(p[k]))
            cr.rectangle(i * sw, h * 0.55, sw + 0.5, h * 0.45)
            cr.fill()


class Preview(Gtk.Overlay):
    """A small mock-up of the desktop: wallpaper, a bar and a terminal window."""

    def __init__(self):
        super().__init__()
        self.set_size_request(-1, 280)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("ts-preview")
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        desk = Gtk.Box(css_classes=["ts-desk"])
        self.picture = SizedPicture(200, 280, min_width=0, hexpand=True, vexpand=True)
        desk.append(self.picture)
        self.set_child(desk)

        self.bar = Gtk.CenterBox(css_classes=["ts-bar"], valign=Gtk.Align.START)
        self.workspaces = Gtk.Label(use_markup=True)
        self.clock = Gtk.Label(label="12:34")
        self.tray = Gtk.Label(use_markup=True)
        self.bar.set_start_widget(self.workspaces)
        self.bar.set_center_widget(self.clock)
        self.bar.set_end_widget(self.tray)
        self.add_overlay(self.bar)

        self.term = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=["ts-term"],
            halign=Gtk.Align.START,
            valign=Gtk.Align.END,
        )
        self.term_text = Gtk.Label(use_markup=True, xalign=0)
        self.term.append(self.term_text)
        self.add_overlay(self.term)

        self.note = Gtk.Label(css_classes=["ts-note"], halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.add_overlay(self.note)

        self.set_palette(None)

    def set_wallpaper(self, path: str):
        if path and Path(path).is_file():
            self.picture.set_visible(True)
            run_bg(lambda: sources.thumbnail(path, 1280), lambda p: p and self.picture.set_filename(str(p)))
        else:
            self.picture.set_filename(None)
            self.picture.set_visible(False)

    def set_palette(self, palette, note: str = ""):
        p = palette or NEUTRAL_PALETTE
        self.note.set_label(note)
        self.note.set_visible(bool(note))
        self.css.load_from_string(
            f"""
            .ts-desk {{ background: {p['background']}; }}
            .ts-bar {{ background: alpha({p['background']}, 0.88); color: {p['foreground']};
                       border-radius: 8px; padding: 3px 12px; margin: 8px; font-size: 9pt; }}
            .ts-term {{ background: alpha({p['background']}, 0.94); color: {p['foreground']};
                        border-radius: 10px; padding: 10px 14px; margin: 16px;
                        border: 2px solid {p['color12']}; font-family: monospace; font-size: 9pt; }}
            .ts-note {{ background: alpha(black, 0.6); color: white; border-radius: 6px;
                        padding: 2px 8px; margin: 10px; font-size: 8pt; }}
            """
        )

        def c(key, text, bold=False):
            weight = " weight='bold'" if bold else ""
            return f"<span foreground='{p[key]}'{weight}>{GLib.markup_escape_text(text)}</span>"

        ws = " ".join(
            f"<span background='{p['color12']}' foreground='{p['background']}'> 1 </span>" if i == 1 else c("foreground", str(i))
            for i in range(1, 6)
        )
        self.workspaces.set_markup(ws)
        self.tray.set_markup(c("color2", "󰕾 72%") + "  " + c("color4", "󰖩") + "  " + c("color3", "󰁹 88%"))
        blocks = "".join(c(f"color{i}", "███") for i in range(8))
        bright = "".join(c(f"color{i}", "███") for i in range(8, 16))
        self.term_text.set_markup(
            "\n".join(
                [
                    c("color2", "you", True) + c("foreground", "@") + c("color4", "hyprland", True) + " " + c("color5", "~") + " $ ls",
                    c("color4", "Documents") + "  " + c("color2", "build.sh") + "  " + c("color3", "notes.md") + "  " + c("color1", "core.dump"),
                    c("color8", "# the sixteen terminal colours"),
                    blocks,
                    bright,
                    c("color2", "you", True) + c("foreground", "@") + c("color4", "hyprland", True) + " " + c("color5", "~") + " $ " + c("cursor", "▌"),
                ]
            )
        )


class ChoiceRow(Adw.ComboRow):
    """A searchable combo bound to a theme key; the first entry ("") means 'leave unchanged'."""

    def __init__(self, title, key, provider, none_label="Unchanged", subtitle="", display=None):
        super().__init__(title=title, subtitle=subtitle)
        self.key = key
        self.provider = provider
        self.none_label = none_label
        self.display = display or (lambda v: v)
        self.values: list[str] = [""]
        self.set_enable_search(True)
        self.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))

    def load(self, value: str):
        values = [""] + list(self.provider())
        labels = [self.none_label] + [self.display(v) for v in values[1:]]
        if value and value not in values:
            values.append(value)
            labels.append(f"{self.display(value)} (missing)")
        self.values = values
        self.set_model(Gtk.StringList.new(labels))
        self.set_selected(values.index(value))

    @property
    def value(self) -> str:
        i = self.get_selected()
        return self.values[i] if 0 <= i < len(self.values) else ""


class ScaleRow(Adw.ActionRow):
    """A slider for one colour adjustment; stores "" when left at the default for the theme."""

    def __init__(self, title, key, attr, lower, upper, step, fmt, subtitle=""):
        super().__init__(title=title, subtitle=subtitle)
        self.key = key
        self.attr = attr  # colors.Modifiers field
        self.fmt = fmt
        self.default = 0.0
        adj = Gtk.Adjustment(lower=lower, upper=upper, step_increment=step, page_increment=step * 5)
        self.scale = Gtk.Scale(adjustment=adj, draw_value=False, width_request=200, valign=Gtk.Align.CENTER)
        self.scale.set_round_digits(0 if step >= 1 else 1)
        self.value_label = Gtk.Label(width_chars=6, xalign=1, css_classes=["dim-label", "numeric"])
        self.scale.connect("value-changed", lambda sc: self.value_label.set_label(fmt(sc.get_value())))
        self.add_suffix(self.scale)
        self.add_suffix(self.value_label)

    def load(self, value: float, default: float):
        self.default = default
        self.scale.clear_marks()
        self.scale.add_mark(default, Gtk.PositionType.BOTTOM, None)
        self.scale.set_value(value)
        self.value_label.set_label(self.fmt(value))

    @property
    def stored(self) -> str:
        v = round(self.scale.get_value(), 1)
        return "" if abs(v - self.default) < 0.05 else f"{v:g}"


class ThemeRow(Gtk.ListBoxRow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)

        self.thumb = Gtk.Stack()
        self.picture = SizedPicture(72, 44, css_classes=["ts-thumb"])
        self.strip = PaletteStrip(72, 44)
        self.thumb.add_named(self.strip, "strip")
        self.thumb.add_named(self.picture, "picture")
        box.append(self.thumb)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "caption"])
        labels.append(self.title)
        labels.append(self.subtitle)
        box.append(labels)

        self.check = Gtk.Image(icon_name="object-select-symbolic", tooltip_text="Currently applied")
        box.append(self.check)
        self.set_child(box)
        self.refresh()

    def refresh(self):
        t = self.theme
        self.title.set_label(t.name)
        self.subtitle.set_label(describe_colors(t))
        self.check.set_visible(t.slug == current_slug())
        wp = t.get("WALLPAPER")
        if wp and Path(wp).is_file():
            self.thumb.set_visible_child_name("picture")
            run_bg(lambda: sources.thumbnail(wp, 160), lambda p: p and self.picture.set_filename(str(p)))
        else:
            self.thumb.set_visible_child_name("strip")
            run_bg(lambda: sources.theme_palette(t), self.strip.set_palette)


# ---------------------------------------------------------------- window


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Theme Studio", default_width=1100, default_height=800)
        self.set_size_request(380, 420)

        self.themes: list[Theme] = []
        self.theme: Theme | None = None  # working copy being edited
        self.saved: dict[str, str] = {}
        self._loading = False
        self._palette_gen = 0
        self._last_palette: dict[str, str] | None = None
        self._selected_row: ThemeRow | None = None
        self._force_close = False

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=260, max_sidebar_width=340)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())

        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self.reload()

    # ---------- layout

    def _build_sidebar(self):
        header = Adw.HeaderBar()
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New theme (Ctrl+N)", action_name="win.new")
        header.pack_start(new_btn)
        menu = Gio.Menu()
        menu.append("Open Themes Folder", "win.open-folder")
        menu.append("Reload", "win.reload")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))

        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.connect("row-selected", self._on_row_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.listbox)

        hint = Gtk.Label(
            label="Super+T picks a theme from anywhere",
            css_classes=["dim-label", "caption"],
            margin_top=6,
            margin_bottom=8,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(hint)

        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Themes", child=view)

    def _build_content(self):
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Theme Studio")
        header.set_title_widget(self.window_title)

        self.apply_btn = Gtk.Button(label="Apply", css_classes=["suggested-action"], action_name="win.apply")
        self.apply_spinner = Adw.Spinner(visible=False)
        self.save_btn = Gtk.Button(label="Save", action_name="win.save")
        menu = Gio.Menu()
        menu.append("Duplicate", "win.duplicate")
        menu.append("Revert Changes", "win.revert")
        menu.append("Delete…", "win.delete")
        self.theme_menu = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        header.pack_end(self.theme_menu)
        header.pack_end(self.apply_btn)
        header.pack_end(self.apply_spinner)
        header.pack_end(self.save_btn)

        self.stack = Gtk.Stack()
        self.stack.add_named(
            Adw.StatusPage(
                icon_name="applications-graphics-symbolic",
                title="No Theme Selected",
                description="Pick a theme on the left, or create a new one with +",
            ),
            "empty",
        )
        self.stack.add_named(self._build_editor(), "editor")

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        return Adw.NavigationPage(title="Theme", child=view)

    def _build_editor(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24)
        clamp = Adw.Clamp(maximum_size=760, child=outer, margin_start=12, margin_end=12)
        scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.preview = Preview()
        outer.append(self.preview)

        # The preview stays on top of both pages, so colour adjustments show as you make them
        self.pages = Adw.ViewStack(vhomogeneous=False)
        outer.append(Adw.InlineViewSwitcher(stack=self.pages, halign=Gtk.Align.CENTER))
        outer.append(self.pages)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.pages.add_titled_with_icon(page, "theme", "Theme", "preferences-desktop-appearance-symbolic")
        self.pages.add_titled_with_icon(self._build_adjust_page(), "colours", "Colours", "applications-graphics-symbolic")

        # --- General
        general = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Theme name")
        self.name_row.connect("changed", lambda r: self._set("NAME", r.get_text().strip()))
        general.add(self.name_row)
        page.append(general)

        # --- Wallpaper
        wp_group = Adw.PreferencesGroup(title="Wallpaper")
        self.wp_row = Adw.ActionRow(title="Image")
        browse = Gtk.Button(label="Browse…", valign=Gtk.Align.CENTER)
        browse.connect("clicked", self._on_browse_wallpaper)
        clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Keep current wallpaper")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._set_wallpaper(""))
        self.wp_row.add_suffix(browse)
        self.wp_row.add_suffix(clear)
        wp_group.add(self.wp_row)

        self.gallery_row = Adw.ExpanderRow(title="Wallpaper library", subtitle="~/Pictures/wallpapers and ~/wallpapers")
        self.gallery = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            homogeneous=True,
            min_children_per_line=3,
            max_children_per_line=6,
            row_spacing=6,
            column_spacing=6,
            margin_top=8,
            margin_bottom=8,
            margin_start=8,
            margin_end=8,
        )
        self.gallery.connect("child-activated", lambda _fb, child: self._set_wallpaper(child.path))
        self.gallery_row.add_row(Gtk.ScrolledWindow(child=self.gallery, min_content_height=300, max_content_height=300))
        self.gallery_row.connect("notify::expanded", self._on_gallery_expanded)
        self._gallery_loaded = False
        wp_group.add(self.gallery_row)
        page.append(wp_group)

        # --- Colours
        color_group = Adw.PreferencesGroup(
            title="Colours",
            description="Feeds Hyprland borders, Waybar, Rofi, Kitty and anything else themed by wallust",
        )
        self.source_row = Adw.ComboRow(title="Colour source", model=Gtk.StringList.new(list(COLOR_SOURCE_LABELS.values())))
        self.source_row.connect("notify::selected", self._on_source_changed)
        color_group.add(self.source_row)
        self.preset_row = ChoiceRow("Preset", "WALLUST_THEME", sources.wallust_presets, none_label="(choose a preset)")
        self.preset_row.connect("notify::selected", lambda r, _p: self._set("WALLUST_THEME", r.value))
        color_group.add(self.preset_row)
        self.adjust_link = Adw.ActionRow(title="Adjust colours", activatable=True)
        self.adjust_link.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.adjust_link.connect("activated", lambda *_: self.pages.set_visible_child_name("colours"))
        color_group.add(self.adjust_link)
        page.append(color_group)

        page.append(self._build_custom_group())

        # --- Apps
        apps = Adw.PreferencesGroup(title="Apps")
        self.choice_rows: list[ChoiceRow] = [
            ChoiceRow("Waybar style", "WAYBAR_STYLE", sources.waybar_styles, display=lambda v: v.removesuffix(".css")),
            ChoiceRow(
                "Kitty theme",
                "KITTY_THEME",
                sources.kitty_themes,
                none_label="Automatic",
                subtitle="Automatic matches the theme's colours",
            ),
            ChoiceRow("Rofi style", "ROFI_THEME", sources.rofi_themes, display=lambda v: v.removesuffix(".rasi")),
        ]
        for row in self.choice_rows:
            apps.add(row)
        page.append(apps)

        # --- Desktop
        desktop = Adw.PreferencesGroup(title="Desktop", description="GTK applications and the mouse cursor")
        desktop_rows = [
            ChoiceRow("GTK theme", "GTK_THEME", sources.gtk_themes),
            ChoiceRow("Icon theme", "ICON_THEME", sources.icon_themes),
            ChoiceRow("Cursor theme", "CURSOR_THEME", sources.cursor_themes),
            ChoiceRow(
                "Light / dark preference",
                "COLOR_SCHEME",
                lambda: ["prefer-dark", "prefer-light", "default"],
                display=lambda v: COLOR_SCHEME_LABELS.get(v, v),
            ),
        ]
        for row in desktop_rows:
            desktop.add(row)
        self.choice_rows += desktop_rows
        page.append(desktop)

        for row in self.choice_rows:
            row.connect("notify::selected", lambda r, _p: self._set(r.key, r.value))

        return scroller

    def _build_custom_group(self):
        self.custom_group = Adw.PreferencesGroup(
            title="Custom palette",
            description="Tip: pick a preset or wallpaper first, then switch to Custom to tweak those colours",
        )
        fill = Gtk.MenuButton(label="Fill From…", valign=Gtk.Align.CENTER, css_classes=["flat"])
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        from_wp = Gtk.Button(label="Current wallpaper")
        from_wp.connect("clicked", self._fill_from_wallpaper)
        pop_box.append(from_wp)
        pop_box.append(Gtk.Separator())
        self.fill_preset = Gtk.DropDown(enable_search=True)
        self.fill_preset.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        pop_box.append(self.fill_preset)
        from_preset = Gtk.Button(label="Use this preset")
        from_preset.connect("clicked", self._fill_from_preset)
        pop_box.append(from_preset)
        self.fill_popover = Gtk.Popover(child=pop_box)
        fill.set_popover(self.fill_popover)
        self.custom_group.set_header_suffix(fill)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12)
        self.color_buttons: dict[str, Gtk.ColorDialogButton] = {}

        for key, label, col, row in PALETTE_GRID:
            btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False, title=label))
            btn.connect("notify::rgba", self._on_custom_color)
            btn.key = key
            self.color_buttons[key] = btn
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(btn)
            box.append(Gtk.Label(label=label, css_classes=["dim-label", "ts-swatch-label"]))
            grid.attach(box, col, row, 1, 1)
        accent_note = Gtk.Label(
            label="Colour 12 is used for window borders and accents",
            css_classes=["dim-label", "caption"],
            margin_bottom=6,
        )
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        frame.append(grid)
        frame.append(accent_note)
        self.custom_group.add(frame)
        return self.custom_group

    def _build_adjust_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.adjust_note = Gtk.Label(
            label="This theme leaves the colours unchanged, so there is nothing to adjust. "
            "Pick a colour source on the Theme page first.",
            wrap=True,
            css_classes=["dim-label"],
        )
        page.append(self.adjust_note)

        group = Adw.PreferencesGroup(
            title="Adjustments",
            description="Applied on top of the colour source, everywhere the colours are used. "
            "Marks under the sliders show the defaults",
        )
        self.reset_adjust = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.reset_adjust.connect("clicked", self._reset_adjustments)
        group.set_header_suffix(self.reset_adjust)

        self.hues_row = Adw.SwitchRow(
            title="Fix terminal colours",
            subtitle="Turn red, green, yellow, blue, magenta and cyan back towards their own hues",
        )
        self.hues_row.connect("notify::active", self._on_hues_toggled)
        group.add(self.hues_row)
        self.scale_rows = [
            ScaleRow("Tint", "COLOR_TINT", "tint", 0, 45, 1, lambda v: f"{v:.0f}°",
                     subtitle="How far fixed colours may lean towards their original colour"),
            ScaleRow("Vibrancy", "COLOR_VIBRANCY", "vibrancy", 0, 200, 5, lambda v: f"{v:.0f}%"),
            ScaleRow("Brightness", "COLOR_BRIGHTNESS", "brightness", -50, 50, 1, lambda v: f"{v:+.0f}",
                     subtitle="Text and terminal colours; the background is unchanged"),
            ScaleRow("Minimum contrast", "COLOR_CONTRAST", "contrast", 0, 10, 0.5,
                     lambda v: "Off" if v < 0.05 else f"{v:.1f}:1",
                     subtitle="Text colours are lightened or darkened to stay readable. 4.5 is the usual standard"),
        ]
        self.tint_row = self.scale_rows[0]
        for row in self.scale_rows:
            row.scale.connect("value-changed", lambda _s, row=row: self._set(row.key, row.stored))
            group.add(row)
        self.adjust_group = group
        page.append(group)

        self.pins_group = Adw.PreferencesGroup(
            title="Specific colours",
            description="Click a colour to pin it. Pinned colours are used exactly as chosen and skip the adjustments",
        )
        unpin_all = Gtk.Button(label="Unpin All", valign=Gtk.Align.CENTER, css_classes=["flat"])
        unpin_all.connect("clicked", lambda *_: self._set_pins({}))
        self.pins_group.set_header_suffix(unpin_all)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12)
        self.pin_buttons: dict[str, Gtk.ColorDialogButton] = {}
        self.unpin_buttons: dict[str, Gtk.Button] = {}
        for key, label, col, row in PALETTE_GRID:
            btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False, title=f"Pin {label}"))
            btn.connect("notify::rgba", self._on_pin_color)
            btn.key = key
            self.pin_buttons[key] = btn
            unpin = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Unpin", css_classes=["flat", "circular", "ts-unpin"])
            unpin.connect("clicked", lambda _b, key=key: self._unpin(key))
            self.unpin_buttons[key] = unpin
            caption = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
            caption.append(Gtk.Label(label=label, css_classes=["dim-label", "ts-swatch-label"]))
            caption.append(unpin)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(btn)
            box.append(caption)
            grid.attach(box, col, row, 1, 1)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        frame.append(grid)
        self.pins_group.add(frame)
        page.append(self.pins_group)
        return page

    # ---------- actions

    def _install_actions(self):
        def add(name, cb, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            self.add_action(a)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", list(accels))
            return a

        add("new", self.new_theme, ["<Ctrl>n"])
        self.act_save = add("save", self.save, ["<Ctrl>s"])
        self.act_apply = add("apply", self.apply, ["<Ctrl>Return"])
        self.act_duplicate = add("duplicate", self.duplicate, ["<Ctrl>d"])
        self.act_revert = add("revert", self.revert)
        self.act_delete = add("delete", self.confirm_delete)
        add("reload", lambda: self.reload(select=self.theme.path if self.theme else None))
        add("open-folder", lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(str(model.THEMES_DIR))).launch(self, None, None))

    # ---------- theme list

    def reload(self, select: Path | None = None):
        self.themes = model.load_all()
        self._loading = True
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        target = None
        if select is None and self.theme is None:
            select = next((t.path for t in self.themes if t.slug == current_slug()), None)
        for t in self.themes:
            row = ThemeRow(t)
            self.listbox.append(row)
            if select and t.path == select:
                target = row
            # The Super+T picker shows these thumbnails
            if not (sources.PICKER_DIR / f"{t.slug}.png").exists():
                run_bg(lambda t=t: sources.write_picker_thumb(t, sources.theme_palette(t)))
        self._loading = False
        if target:
            self._selected_row = target
            self._loading = True
            self.listbox.select_row(target)
            self._loading = False
            if self.theme is None:
                self.load_theme(target.theme)
        elif self.theme is None or self.theme.path is not None:
            self._selected_row = None
            self.load_theme(None)
        self._update_state()

    def _on_row_selected(self, _lb, row):
        if self._loading or row is self._selected_row:
            return
        if row is None:
            return
        if self.is_dirty():
            previous = self._selected_row
            self._loading = True
            if previous:
                self.listbox.select_row(previous)
            else:
                self.listbox.unselect_all()
            self._loading = False
            self.ask_unsaved(lambda: self._select_row(row))
            return
        self._select_row(row)

    def _select_row(self, row):
        self._selected_row = row
        self._loading = True
        self.listbox.select_row(row)
        self._loading = False
        self.load_theme(row.theme)
        self.split.set_show_content(True)

    # ---------- editor state

    def load_theme(self, theme: Theme | None):
        self.theme = theme.copy() if theme else None
        self.saved = self._normalized(theme) if theme and theme.path else {}
        if not self.theme:
            self.stack.set_visible_child_name("empty")
            self._update_state()
            return
        self.stack.set_visible_child_name("editor")
        self._loading = True
        t = self.theme
        self.name_row.set_text(t.get("NAME"))
        self.source_row.set_selected(list(COLOR_SOURCE_LABELS).index(t.color_source))
        self.preset_row.load(t.get("WALLUST_THEME"))
        for row in self.choice_rows:
            row.load(t.get(row.key))
        self.fill_preset.set_model(Gtk.StringList.new(sources.wallust_presets()))
        self._load_custom_buttons(t.custom_palette or DEFAULT_PALETTE)
        self._load_adjustments()
        self._loading = False
        self._show_wallpaper()
        self._update_visibility()
        self._refresh_palette()
        self._update_state()

    def _normalized(self, theme: Theme | None) -> dict[str, str]:
        if not theme:
            return {}
        return {k: v for k, v in theme.values.items() if v}

    def is_dirty(self) -> bool:
        if not self.theme:
            return False
        return self.theme.path is None or self._normalized(self.theme) != self.saved

    def _set(self, key, value):
        if self._loading or not self.theme:
            return
        self.theme.set(key, value)
        if key in PALETTE_INPUT_KEYS:
            self._refresh_palette()
        if key in ADJUST_KEYS:
            self._update_visibility()
        self._update_state()

    def _update_state(self):
        has = self.theme is not None
        dirty = self.is_dirty()
        self.act_save.set_enabled(dirty)
        self.act_apply.set_enabled(has)
        self.act_duplicate.set_enabled(has)
        self.act_revert.set_enabled(has and dirty and self.theme.path is not None)
        self.act_delete.set_enabled(has)
        self.theme_menu.set_sensitive(has)
        if has:
            self.window_title.set_title(self.theme.name)
            self.window_title.set_subtitle("Unsaved changes" if dirty else "")
        else:
            self.window_title.set_title("Theme Studio")
            self.window_title.set_subtitle("")

    def _update_visibility(self):
        src = self.theme.color_source if self.theme else "none"
        self.preset_row.set_visible(src == "preset")
        self.custom_group.set_visible(src == "custom")
        if src == "wallpaper" and not self.theme.get("WALLPAPER"):
            self.source_row.set_subtitle("Choose a wallpaper to generate colours from")
        else:
            self.source_row.set_subtitle("")
        self.adjust_link.set_visible(src != "none")
        self.adjust_link.set_subtitle(
            "Adjusted" if self.theme and any(self.theme.get(k) for k in ADJUST_KEYS) else "Vibrancy, contrast and pinned colours"
        )
        self.adjust_note.set_visible(src == "none")
        self.adjust_group.set_sensitive(src != "none")
        self.pins_group.set_sensitive(src != "none")
        self.tint_row.set_sensitive(self.hues_row.get_active())
        self.reset_adjust.set_sensitive(bool(self.theme) and any(self.theme.get(k) for k in ADJUST_KEYS[:-1]))

    # ---------- wallpaper

    def _show_wallpaper(self):
        wp = self.theme.get("WALLPAPER")
        if wp:
            self.wp_row.set_subtitle(GLib.markup_escape_text(wp.replace(str(Path.home()), "~")))
        else:
            self.wp_row.set_subtitle("Unchanged — keeps whatever wallpaper is showing")
        self.preview.set_wallpaper(wp)
        self._sync_gallery_selection()

    def _set_wallpaper(self, path: str):
        if not self.theme:
            return
        self._set("WALLPAPER", path)
        self._show_wallpaper()
        self._update_visibility()

    def _on_browse_wallpaper(self, _btn):
        dialog = Gtk.FileDialog(title="Choose Wallpaper")
        filt = Gtk.FileFilter(name="Images")
        filt.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        start = sources.WALLPAPER_DIRS[0]
        if start.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(start)))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self._set_wallpaper(f.get_path())

        dialog.open(self, None, done)

    def _on_gallery_expanded(self, row, _pspec):
        if not row.get_expanded() or self._gallery_loaded:
            return
        self._gallery_loaded = True
        for path in sources.wallpapers():
            pic = SizedPicture(110, 70, css_classes=["ts-gallery-item"])
            child = Gtk.FlowBoxChild(child=pic, tooltip_text=path.name)
            child.path = str(path)
            self.gallery.append(child)
            run_bg(lambda p=path: sources.thumbnail(p, 240), lambda t, pic=pic: t and pic.set_filename(str(t)))
        self._sync_gallery_selection()

    def _sync_gallery_selection(self):
        wp = self.theme.get("WALLPAPER") if self.theme else ""
        self.gallery.unselect_all()
        child = self.gallery.get_first_child()
        while child is not None:
            if getattr(child, "path", None) == wp:
                self.gallery.select_child(child)
                break
            child = child.get_next_sibling()

    # ---------- colours

    def _on_source_changed(self, row, _pspec):
        if self._loading or not self.theme:
            return
        src = list(COLOR_SOURCE_LABELS)[row.get_selected()]
        if src == "custom" and not self.theme.custom_palette:
            # Start the custom palette from whatever is currently previewed
            palette = self._last_palette or DEFAULT_PALETTE
            self._load_custom_buttons(palette)
            self.theme.set_custom_palette(palette)
        self._set("COLOR_SOURCE", src)
        self._load_adjustments()  # blank settings follow the new source's defaults
        self._update_visibility()

    def _load_custom_buttons(self, palette):
        was = self._loading
        self._loading = True
        for key, btn in self.color_buttons.items():
            btn.set_rgba(rgba(palette[key]))
        self._loading = was

    def _on_custom_color(self, _btn, _pspec):
        if self._loading or not self.theme:
            return
        palette = {k: rgba_hex(b.get_rgba()) for k, b in self.color_buttons.items()}
        self.theme.set_custom_palette(palette)
        self._set("CUSTOM_COLORS", self.theme.get("CUSTOM_COLORS"))

    def _apply_fill(self, palette):
        if not palette:
            self.toast("Couldn't read colours from that source")
            return
        self._load_custom_buttons(palette)
        self.theme.set_custom_palette(palette)
        self._set("CUSTOM_COLORS", self.theme.get("CUSTOM_COLORS"))

    def _fill_from_wallpaper(self, _btn):
        self.fill_popover.popdown()
        wp = self.theme.get("WALLPAPER")
        if not wp:
            self.toast("Choose a wallpaper first")
            return
        defaults = colors.Modifiers.defaults("wallpaper")
        run_bg(lambda: (p := sources.wallpaper_palette(wp)) and colors.adjust(p, defaults), self._apply_fill)

    def _fill_from_preset(self, _btn):
        self.fill_popover.popdown()
        item = self.fill_preset.get_selected_item()
        if item:
            name = item.get_string()
            run_bg(lambda: sources.preset_palette(name), self._apply_fill)

    def _refresh_palette(self):
        if not self.theme:
            return
        self._palette_gen += 1
        gen = self._palette_gen
        snap = self.theme.copy()
        src = snap.color_source

        def done(palette):
            if gen != self._palette_gen:
                return
            if palette:
                self._last_palette = palette
            note = {"none": "Colours unchanged — preview uses neutral colours"}.get(src, "")
            if src == "preset" and not snap.get("WALLUST_THEME"):
                note = "Choose a preset"
            elif src == "wallpaper" and not palette:
                note = "No wallpaper to generate colours from"
            self.preview.set_palette(palette, note)
            self._load_pin_buttons(palette or NEUTRAL_PALETTE)

        run_bg(lambda: sources.theme_palette(snap), done)

    # ---------- colour adjustments

    def _load_adjustments(self):
        t = self.theme
        mods = colors.Modifiers.from_theme(t)
        defaults = colors.Modifiers.defaults(t.color_source)
        was = self._loading
        self._loading = True
        self.hues_row.set_active(mods.hues)
        for row in self.scale_rows:
            row.load(getattr(mods, row.attr), getattr(defaults, row.attr))
        self._loading = was
        self._load_pin_buttons(self._last_palette or NEUTRAL_PALETTE)

    def _on_hues_toggled(self, row, _pspec):
        if self._loading or not self.theme:
            return
        default = colors.Modifiers.defaults(self.theme.color_source).hues
        self._set("COLOR_HUES", "" if row.get_active() == default else ("on" if row.get_active() else "off"))

    def _reset_adjustments(self, _btn):
        for key in ADJUST_KEYS[:-1]:  # pinned colours have their own Unpin All
            self.theme.set(key, "")
        self._load_adjustments()
        self._set("COLOR_HUES", "")

    def _pins(self) -> dict[str, str]:
        return colors.parse_overrides(self.theme.get("COLOR_OVERRIDES")) if self.theme else {}

    def _set_pins(self, pins: dict[str, str]):
        self._set("COLOR_OVERRIDES", colors.format_overrides(pins))
        self._load_pin_buttons(self._last_palette or NEUTRAL_PALETTE)

    def _on_pin_color(self, btn, _pspec):
        if self._loading or not self.theme:
            return
        self._set_pins({**self._pins(), btn.key: rgba_hex(btn.get_rgba())})

    def _unpin(self, key):
        pins = self._pins()
        pins.pop(key, None)
        self._set_pins(pins)

    def _load_pin_buttons(self, palette):
        """Show the theme's final colours; pinned ones get an unpin button."""
        pins = self._pins()
        was = self._loading
        self._loading = True
        for key, btn in self.pin_buttons.items():
            btn.set_rgba(rgba(pins.get(key) or palette[key]))
            self.unpin_buttons[key].set_visible(key in pins)
        self._loading = was

    # ---------- commands

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3, use_markup=False))

    def new_theme(self):
        self._guard_unsaved(lambda: self._start_unsaved(Theme(values={"NAME": "New Theme", "COLOR_SOURCE": "wallpaper"})))

    def duplicate(self):
        if not self.theme:
            return
        copy = Theme(values=dict(self.theme.values))
        copy.set("NAME", f"{self.theme.name} Copy")
        self._guard_unsaved(lambda: self._start_unsaved(copy))

    def _start_unsaved(self, theme: Theme):
        self._loading = True
        self.listbox.unselect_all()
        self._loading = False
        self._selected_row = None
        self.load_theme(theme)
        self.split.set_show_content(True)
        self.name_row.grab_focus()

    def save(self) -> bool:
        if not self.theme:
            return False
        if not self.theme.get("NAME"):
            self.toast("Give the theme a name first")
            return False
        old_slug = self.theme.slug if self.theme.path else None
        path = model.save(self.theme)
        if old_slug and old_slug != self.theme.slug:
            sources.remove_picker_thumb(old_slug)
        saved = self.theme.copy()
        run_bg(lambda: sources.write_picker_thumb(saved, sources.theme_palette(saved)))
        self.saved = self._normalized(self.theme)
        self.reload(select=path)
        self.toast(f"Saved “{self.theme.name}”")
        return True

    def revert(self):
        if self._selected_row:
            self.load_theme(self._selected_row.theme)

    def apply(self):
        if not self.theme:
            return
        if self.is_dirty() and not self.save():
            return
        name, path = self.theme.name, self.theme.path
        self.apply_btn.set_sensitive(False)
        self.apply_spinner.set_visible(True)
        # setsid so waybar & co. restarted by the script don't belong to this window
        proc = Gio.Subprocess.new(["setsid", "--wait", str(THEME_APPLY), str(path)], Gio.SubprocessFlags.NONE)

        def done(p, res):
            self.apply_btn.set_sensitive(True)
            self.apply_spinner.set_visible(False)
            try:
                p.wait_check_finish(res)
                self.toast(f"Applied “{name}”")
            except GLib.Error:
                self.toast(f"Applying “{name}” failed — see notifications")
            self._refresh_row_marks()

        proc.wait_check_async(None, done)

    def _refresh_row_marks(self):
        i = 0
        while (row := self.listbox.get_row_at_index(i)) is not None:
            row.check.set_visible(row.theme.slug == current_slug())
            i += 1

    def confirm_delete(self):
        if not self.theme:
            return
        if self.theme.path is None:  # an unsaved draft: just drop it
            self.load_theme(None)
            return
        d = Adw.AlertDialog(heading=f"Delete “{self.theme.name}”?", body="The theme file will be removed. Wallpapers are not touched.")
        d.add_response("cancel", "Cancel")
        d.add_response("delete", "Delete")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")

        def on_response(_d, resp):
            if resp == "delete":
                model.delete(self.theme)
                sources.remove_picker_thumb(self.theme.slug)
                self.toast(f"Deleted “{self.theme.name}”")
                self.theme = None
                self.reload()

        d.connect("response", on_response)
        d.present(self)

    # ---------- unsaved changes

    def ask_unsaved(self, then):
        d = Adw.AlertDialog(heading="Save changes?", body=f"“{self.theme.name}” has unsaved changes.")
        d.add_response("cancel", "Cancel")
        d.add_response("discard", "Discard")
        d.add_response("save", "Save")
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")
        d.set_close_response("cancel")

        def on_response(_d, resp):
            if resp == "save" and not self.save():
                return
            if resp in ("save", "discard"):
                self.saved = self._normalized(self.theme)
                if self.theme.path is None:
                    self.theme = None
                then()

        d.connect("response", on_response)
        d.present(self)

    def _guard_unsaved(self, then):
        if self.is_dirty():
            self.ask_unsaved(then)
        else:
            then()

    def _on_close_request(self, _win):
        if self._force_close or not self.is_dirty():
            return False

        def close():
            self._force_close = True
            self.close()

        self.ask_unsaved(close)
        return True


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.get_active_window() and self.get_active_window().close())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


def main() -> int:
    return App().run(sys.argv)
