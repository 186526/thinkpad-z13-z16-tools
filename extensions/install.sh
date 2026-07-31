#!/usr/bin/env bash
# 安装 z13-touchpad-quick@user 扩展到 ~/.local/share/gnome-shell/extensions/。
# 幂等:已存在则整体覆盖重装。仅复制本扩展目录,不动仓库其它文件。
#
# 说明(GNOME 50 实测):
# - 运行中的 shell 只在启动时扫描一次扩展目录,安装后不会立即感知;
#   手动 cp 或 gnome-extensions install 都一样,Alt+F2→r 重载命令在
#   GNOME 42+ 已移除。因此本脚本最后一步把 UUID 写入 GNOME 的启用列表,
#   下次登录/重启后 shell 扫描到扩展时会自动启用,无需再手动操作。
set -euo pipefail

UUID="z13-touchpad-quick@user"
SRC="$(cd "$(dirname "$0")" && pwd)/$UUID"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

if [ ! -d "$SRC" ]; then
    echo "错误:找不到扩展源码目录 $SRC" >&2
    exit 1
fi

# 解析 z13-touchpad-apply 的绝对路径:优先 PATH,否则仓库内脚本。
# 写进安装目录的 cli-path 文件,扩展加载时优先使用。
CLI="$(command -v z13-touchpad-apply || true)"
if [ -z "$CLI" ]; then
    CLI="$(cd "$(dirname "$0")/.." && pwd)/z13-touchpad-apply"
fi
if [ ! -x "$CLI" ]; then
    echo "警告:找不到可执行的 z13-touchpad-apply($CLI)。" >&2
    echo "扩展会加载但开关无效;请把 z13-touchpad-apply 加入 PATH 后重跑本脚本。" >&2
fi

# 打包含 cli-path 的 zip,用官方 gnome-extensions install 安装;
# 工具或 python3 不可用时回退到直接复制。
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -r "$SRC"/. "$STAGE"/
printf '%s\n' "$CLI" > "$STAGE/cli-path"

if command -v gnome-extensions >/dev/null 2>&1 \
    && command -v python3 >/dev/null 2>&1 \
    && (cd "$STAGE" && python3 -m zipfile -c "$STAGE.zip" .) \
    && gnome-extensions install --force "$STAGE.zip" >/dev/null 2>&1; then
    echo "已安装到 $DEST(gnome-extensions install)"
else
    rm -rf "$DEST"
    mkdir -p "$DEST"
    cp -r "$SRC"/. "$DEST"/
    printf '%s\n' "$CLI" > "$DEST/cli-path"
    echo "已安装到 $DEST(直接复制)"
fi

# 启用:若运行中的 shell 已感知(本次 shell 启动前就装过),直接启用;
# 否则把 UUID 追加进 GNOME 启用列表(保留其它已启用扩展),
# 下次登录/重启后 shell 扫描到扩展时自动启用。
if gnome-extensions enable "$UUID" >/dev/null 2>&1; then
    echo "已启用,打开快速设置面板查看\"触感强度\"开关。"
else
    if python3 - "$UUID" <<'PY'
import subprocess, sys
uuid = sys.argv[1]
out = subprocess.run(
    ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
    capture_output=True, text=True).stdout.strip()
try:
    enabled = eval(out, {"__builtins__": {}}) if out.startswith("[") else []
except Exception:
    enabled = []
if uuid not in enabled:
    enabled.append(uuid)
    subprocess.run(
        ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
         str(enabled)], check=True)
PY
    then
        echo "已写入启用列表:下次登录/重启后自动生效,无需再手动启用。"
        echo "当前 shell 是安装前启动的,若要立即生效请注销重新登录,"
        echo "打开快速设置面板查看\"触感强度\"开关。"
    else
        echo "启用失败:请注销重新登录后运行:" >&2
        echo "    gnome-extensions enable $UUID" >&2
    fi
fi
