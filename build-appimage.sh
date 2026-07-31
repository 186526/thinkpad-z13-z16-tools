#!/usr/bin/env bash
# 构建 Z13/Z16 Gen 2 工具箱的自包含 AppImage。
#
# 手工组装 AppDir（Python 解释器 + PyGObject/GTK4/libadwaita 系统库 + 项目文件），
# 再用 appimagetool 打包。本地与 GitHub Actions CI 共用同一脚本。
#
# 用法:  VERSION=v0.1.0 ./build-appimage.sh
# 产物:  dist/thinkpad-z13-z16-tools-<VERSION>.AppImage
set -euo pipefail
trap 'echo "ERR at line $LINENO: $BASH_COMMAND"' ERR

# ---------- 常量 ----------
APP_NAME="thinkpad-z13-z16-tools"
APP_TITLE="Z13/Z16 Gen 2 工具箱"
DESKTOP_ID="io.github.thinkpad-z13-z16-tools"
VERSION="${VERSION:-$(git describe --tags --always 2>/dev/null || echo dev)}"
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DIST="$ROOT/dist"
APPDIR="$DIST/AppDir"
ARCH_DIR="x86_64-linux-gnu"          # Debian/Ubuntu 多架构目录名
LIBDIR="$APPDIR/usr/lib/$ARCH_DIR"

# 需要打包进 AppImage 的项目文件
PROJECT_FILES=(
  haptic.py gui.py gui_utils.py touchpad_map.py brightness.py resume-watch.py
  feature-probe.py gpu-reset-watch.py hw-probe.py blob-sweep.py
  z13-touchpad-apply z13-brightness-apply z13-camera-tool z13-touchpad-tool
)

# GTK 运行期 dlopen（不在静态依赖链里）需要显式收集的库
EXTRA_LIBS=(
  libgtk-4.so.1 libadwaita-1.so.0 libgdk_pixbuf-2.0.so.0
  libwayland-client.so.0 libxkbcommon.so.0 libX11.so.6 libX11-xcb.so.1
  libxcb.so.1 libxcb-cursor.so.0 libxkbcommon-x11.so.0 libXrandr.so.2
  libXinerama.so.1 libXi.so.6 libXext.so.6 libXfixes.so.3 libXdamage.so.1
  libXrender.so.1 libXcursor.so.1 libXcomposite.so.1 libxshmfence.so.1
  libgraphene-1.0.so.0 libpango-1.0.so.0 libpangocairo-1.0.so.0
  libpangoft2-1.0.so.0 libharfbuzz.so.0 libharfbuzz-subset.so.0
  libfontconfig.so.1 libfribidi.so.0 libcairo.so.2 libcairo-gobject.so.2
  libepoxy.so.0 libtiff.so.6 libjpeg.so.62 libpng16.so.16
  libzstd.so.1 libbrotlidec.so.1 libbrotlicommon.so.1
)

log() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }

# ---------- 递归收集动态库（保留 soname 文件名，解引用真实文件） ----------
declare -A _seen_libs
collect_libs() {
  local so
  for so in "$@"; do
    [ -e "$so" ] || continue
    local real
    real="$(readlink -f "$so")"
    if [ -n "${_seen_libs[$real]:-}" ]; then
      continue  # 已处理过，跳过
    fi
    _seen_libs[$real]=1
    if [ -e "$LIBDIR/$(basename "$real")" ]; then
      : # 已拷贝过
    else
      mkdir -p "$LIBDIR"
      cp -L "$real" "$LIBDIR/$(basename "$real")"
    fi
    # 递归依赖
    local deps
    deps="$(ldd "$real" 2>/dev/null | awk '/=> \//{print $3} /^\t\/[^ ]/{print $1}' || true)"
    if [ -n "$deps" ]; then
      # shellcheck disable=SC2086
      collect_libs $deps
    fi
  done
}

# ---------- 1. 组装基础目录 ----------
rm -rf "$DIST/AppDir"
mkdir -p "$APPDIR/usr/bin" "$LIBDIR"

log "拷贝项目文件"
for f in "${PROJECT_FILES[@]}"; do
  cp "$ROOT/$f" "$APPDIR/usr/bin/"
done

log "拷贝 Python 解释器与标准库"
PYBIN="$(command -v python3)"
cp -L "$PYBIN" "$APPDIR/usr/bin/python3"
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PYSTDLIB="$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
cp -r "$PYSTDLIB" "$APPDIR/usr/lib/python$PYV"
collect_libs "$(readlink -f "$PYBIN")"
collect_libs "/usr/lib/$ARCH_DIR/libpython${PYV}.so.1"

log "拷贝 PyGObject / pycairo 包"
# 用运行时路径定位（Debian 上可能装在 /usr/lib/python3/dist-packages）
MODDIR="$(python3 -c 'import gi, cairo, os; print(os.path.dirname(os.path.dirname(gi.__file__)))')"
SITE="$APPDIR/usr/lib/python$PYV/site-packages"
mkdir -p "$SITE"
for mod in gi cairo; do
  if [ -d "$MODDIR/$mod" ]; then
    cp -r "$MODDIR/$mod" "$SITE/"
  fi
done
# 包内二进制模块（_gi*.so / _cairo*.so）也收集其动态依赖
for so in "$SITE"/gi/_gi*.so "$SITE"/gi/_gi_cairo*.so "$SITE"/cairo/_cairo*.so; do
  [ -e "$so" ] && collect_libs "$so" || true
done
# gi 运行期可能需要的纯 python 依赖
for dep in typing_extensions; do
  [ -d "$MODDIR/$dep" ] && cp -r "$MODDIR/$dep" "$SITE/" || true
done

log "收集 GTK 运行库（含 dlopen 依赖）"
for so in "${EXTRA_LIBS[@]}"; do
  # 在系统库路径中解析
  resolved="$(ldconfig -p 2>/dev/null | awk -v n="$so" '$1==n{print $NF; exit}')" || true
  [ -n "$resolved" ] && collect_libs "$resolved" || true
done
# 显式补一轮常见依赖
collect_libs /usr/lib/$ARCH_DIR/libglib-2.0.so.0 \
             /usr/lib/$ARCH_DIR/libgobject-2.0.so.0 \
             /usr/lib/$ARCH_DIR/libgio-2.0.so.0 \
             /usr/lib/$ARCH_DIR/libgirepository-1.0.so.1 \
             /usr/lib/$ARCH_DIR/libgmodule-2.0.so.0 \
             /usr/lib/$ARCH_DIR/libffi.so.8

log "拷贝 gdk-pixbuf PNG 加载器"
PIXBUF_LD="$(find /usr/lib/$ARCH_DIR/gdk-pixbuf-2.0 -name 'libpixbufloader-png.so' 2>/dev/null | head -1)"
if [ -n "$PIXBUF_LD" ]; then
  mkdir -p "$APPDIR/usr/lib/$ARCH_DIR/gdk-pixbuf-2.0/2.10.0/loaders"
  cp -L "$PIXBUF_LD" "$APPDIR/usr/lib/$ARCH_DIR/gdk-pixbuf-2.0/2.10.0/loaders/"
  collect_libs "$PIXBUF_LD"
fi

log "拷贝 GI typelib（Gtk-4.0 / Adw-1.0 等）"
GI_DIR="/usr/lib/$ARCH_DIR/girepository-1.0"
mkdir -p "$LIBDIR/girepository-1.0"
for t in Gtk-4.0 Adw-1.0 Gdk-4.0 GdkPixbuf-2.0 Pango-1.0 PangoCairo-1.0 HarfBuzz-0.0 Graphene-1.0 GLib-2.0 GObject-2.0 Gio-2.0 cairo-1.0 freetype2-2.0; do
  [ -e "$GI_DIR/$t.typelib" ] && cp "$GI_DIR/$t.typelib" "$LIBDIR/girepository-1.0/" || true
done

log "拷贝并编译 GSettings schemas（GTK4/libadwaita 必需）"
SCHEMA_DIR="$APPDIR/usr/share/glib-2.0/schemas"
mkdir -p "$SCHEMA_DIR"
for s in /usr/share/glib-2.0/schemas/org.gtk.gtk4*.gschema.xml \
         /usr/share/glib-2.0/schemas/org.gnome.adw*.gschema.xml \
         /usr/share/glib-2.0/schemas/org.glib*.gschema.xml; do
  [ -e "$s" ] && cp "$s" "$SCHEMA_DIR/" || true
done
if command -v glib-compile-schemas >/dev/null; then
  glib-compile-schemas "$SCHEMA_DIR"
else
  log "警告: 未找到 glib-compile-schemas，跳过 schemas 编译"
fi

log "拷贝 Adwaita 图标主题"
if [ -d /usr/share/icons/Adwaita ]; then
  cp -r /usr/share/icons/Adwaita "$APPDIR/usr/share/icons/"
else
  log "警告: 系统无 Adwaita 图标主题"
fi

# ---------- 2. AppRun / .desktop / icon ----------
log "生成 AppRun 与桌面文件"
cat > "$APPDIR/AppRun" <<EOF
#!/usr/bin/env bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
export LD_LIBRARY_PATH="\$HERE/usr/lib/$ARCH_DIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="\$HERE/usr/lib/$ARCH_DIR/girepository-1.0\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}"
export GSETTINGS_SCHEMA_DIR="\$HERE/usr/share/glib-2.0/schemas"
export GDK_PIXBUF_MODULEDIR="\$HERE/usr/lib/$ARCH_DIR/gdk-pixbuf-2.0/2.10.0/loaders"
export XDG_DATA_DIRS="\$HERE/usr/share\${XDG_DATA_DIRS:+:\$XDG_DATA_DIRS}"
export PYTHONHOME="\$HERE/usr"
export PYTHONPATH="\$HERE/usr/bin:\$HERE/usr/lib/python$PYV/site-packages"
exec "\$HERE/usr/bin/python3" "\$HERE/usr/bin/gui.py" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

mkdir -p "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/128x128/apps"
cat > "$APPDIR/usr/share/applications/$DESKTOP_ID.desktop" <<EOF
[Desktop Entry]
Name=$APP_TITLE
Name[en]=Z13/Z16 Gen 2 Toolbox
Comment=ThinkPad Z13/Z16 Gen 2 Sensel haptic touchpad, OLED brightness and companion tools
Exec=AppRun
Icon=$DESKTOP_ID
Terminal=false
Type=Application
Categories=Settings;HardwareSettings;Utility;
StartupWMClass=io.github.thinkpad-z13-z16-tools
EOF
cp "$ROOT/packaging/icon-128.png" \
   "$APPDIR/usr/share/icons/hicolor/128x128/apps/$DESKTOP_ID.png" || true

# 顶层 Icon 供 appimagetool 使用
[ -f "$ROOT/packaging/icon-128.png" ] && cp "$ROOT/packaging/icon-128.png" "$APPDIR/$DESKTOP_ID.png" || true
# appimagetool 需要根目录的 .desktop
cp "$APPDIR/usr/share/applications/$DESKTOP_ID.desktop" "$APPDIR/"

# ---------- 3. 打包 ----------
log "用 appimagetool 打包"
APPIMAGETOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$APPIMAGETOOL" ]; then
  log "错误: 未找到 appimagetool，请设置 APPIMAGETOOL=/path/to/appimagetool"
  exit 1
fi
export ARCH=x86_64
mkdir -p "$DIST"
"$APPIMAGETOOL" --no-appstream \
  "$APPDIR" "$DIST/${APP_NAME}-${VERSION}.AppImage"

log "完成: $DIST/${APP_NAME}-${VERSION}.AppImage"
