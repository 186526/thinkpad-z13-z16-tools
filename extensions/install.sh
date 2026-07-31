#!/usr/bin/env bash
# 安装 z13-touchpad-quick@user 扩展到 ~/.local/share/gnome-shell/extensions/。
# 幂等:已存在则整体覆盖重装。仅复制本扩展目录,不动仓库其它文件。
set -euo pipefail

UUID="z13-touchpad-quick@user"
SRC="$(cd "$(dirname "$0")" && pwd)/$UUID"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

if [ ! -d "$SRC" ]; then
    echo "错误:找不到扩展源码目录 $SRC" >&2
    exit 1
fi

# 解析 z13-touchpad-apply 的绝对路径:优先 PATH,否则仓库内脚本。
# 写进已安装目录的 cli-path 文件,扩展加载时优先使用。
CLI="$(command -v z13-touchpad-apply || true)"
if [ -z "$CLI" ]; then
    CLI="$(cd "$(dirname "$0")/.." && pwd)/z13-touchpad-apply"
fi
if [ ! -x "$CLI" ]; then
    echo "警告:找不到可执行的 z13-touchpad-apply($CLI)。" >&2
    echo "扩展会加载但开关无效;请把 z13-touchpad-apply 加入 PATH 后重跑本脚本。" >&2
fi

rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$SRC"/. "$DEST"/
printf '%s\n' "$CLI" > "$DEST/cli-path"

echo "已安装到 $DEST"
echo
echo "启用扩展:"
echo "    gnome-extensions enable $UUID"
echo "然后重新加载 shell(Alt+F2 输入 r 回车)或重新登录,"
echo "打开快速设置面板查看\"触感强度\"开关。"
