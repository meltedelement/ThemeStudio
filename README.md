# Theme Studio

Create, edit and switch Hyprland theme bundles. A bundle sets a wallpaper, a colour palette
(applied through wallust, so it reaches Hyprland borders, Waybar, Rofi, Kitty, Ghostty and cava),
a Waybar style, a Kitty theme, a Rofi style, and GTK, icon and cursor themes.

| Command        | What it does                                                    |
| -------------- | --------------------------------------------------------------- |
| `theme-studio` | GTK4 editor: create, edit, duplicate, delete and apply themes   |
| `theme-pick`   | Rofi picker with thumbnails (bound to **Super+T**)              |
| `theme-apply`  | Apply one bundle: `theme-apply Moonrise` or a path to `.theme`  |

## Install

```sh
./install.sh
```

This links the three commands into `~/.local/bin`, adds a "Theme Studio" launcher entry, and
makes `~/.config/hypr/scripts/ThemeBundleSwitcher.sh` (Super+T) call `theme-pick`. The previous
script is backed up next to it.

## Theme files

Themes live in `~/.config/hypr/themes/*.theme` as bash-style `KEY="value"` files. Blank values
leave that part of the desktop as it is.

```sh
NAME="Moonrise"
WALLPAPER="/home/you/Pictures/wallpapers/Moonrise-Pines.jpg"
COLOR_SOURCE="wallpaper"          # wallpaper | preset | custom | none
WALLUST_THEME=""                  # used when COLOR_SOURCE=preset
CUSTOM_COLORS=""                  # used when COLOR_SOURCE=custom: bg fg cursor color0..color15
COLOR_HUES=""                     # on | off: fix terminal colours (blank: on for wallpaper themes)
COLOR_TINT=""                     # 0-45: degrees fixed colours lean towards the original (15)
COLOR_VIBRANCY=""                 # 0-200 percent (100)
COLOR_BRIGHTNESS=""               # -50..50, text colours only (0)
COLOR_CONTRAST=""                 # minimum contrast for text, 0 = off (blank: 4.5 for wallpaper themes)
COLOR_OVERRIDES=""                # pinned colours, e.g. "color4=#7AA2F7 background=#101018"
WAYBAR_STYLE="[Wallust Transparent] Crystal Clear.css"
KITTY_THEME=""                    # blank = follow the theme's colours (01-Wallust)
ROFI_THEME="KooL_style-4.rasi"
GTK_THEME=""
ICON_THEME=""
CURSOR_THEME=""
COLOR_SCHEME=""                   # prefer-dark | prefer-light | default
```

Every palette goes through the theme's colour adjustments (`themestudio/colors.py`) before wallust
renders any template, so Kitty, Ghostty, Waybar, Rofi and Hyprland all get the same colours. Edit
them on the **Colours** page in Theme Studio. Blank settings use the default for the colour
source. Wallpaper themes get their terminal colours turned back towards red, green, blue and so on,
and are kept readable against the background. Presets and custom palettes are left as made unless
you change something.

Files without `COLOR_SOURCE` work as before: custom colours, then preset, then wallpaper.

Wallpapers for the library are read from `~/Pictures/wallpapers` and `~/wallpapers`.
Thumbnails and generated colour schemes are cached in `~/.cache/themestudio/`.
