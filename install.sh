#!/usr/bin/env bash
# Installs Theme Studio for the current user:
#   - symlinks theme-studio / theme-apply / theme-pick into ~/.local/bin
#   - adds an app launcher entry
#   - points the existing Super+T script (ThemeBundleSwitcher.sh) at theme-pick
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
SWITCHER="$HOME/.config/hypr/scripts/ThemeBundleSwitcher.sh"

mkdir -p "$BIN" "$APPS"
chmod +x "$HERE"/bin/*
for f in theme-studio theme-apply theme-pick; do
  ln -sfn "$HERE/bin/$f" "$BIN/$f"
  echo "linked $BIN/$f"
done

cat >"$APPS/dev.meltedelement.ThemeStudio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Theme Studio
Comment=Create, edit and apply desktop themes
Exec=$BIN/theme-studio
Icon=preferences-desktop-theme
Categories=Settings;DesktopSettings;GTK;
Keywords=theme;wallpaper;colors;waybar;kitty;rofi;
StartupNotify=true
EOF
echo "wrote $APPS/dev.meltedelement.ThemeStudio.desktop"

if [[ -f "$SWITCHER" ]] && ! grep -q 'theme-pick' "$SWITCHER"; then
  backup="$SWITCHER.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$SWITCHER" "$backup"
  echo "backed up old switcher to $backup"
fi
cat >"$SWITCHER" <<EOF
#!/usr/bin/env bash
# /* ---- 💫 Theme bundle switcher (Super+T) 💫 ---- */
# Managed by Theme Studio ($HERE). Edit themes with: theme-studio
exec "$BIN/theme-pick" "\$@"
EOF
chmod +x "$SWITCHER"
echo "Super+T now runs theme-pick"
