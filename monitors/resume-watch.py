#!/usr/bin/env python3
"""Re-apply the saved touchpad config after suspend / hibernate.

The touchpad's firmware registers are RAM-only and revert to defaults on
suspend, but the login-time z13-touchpad-haptic.service only covers boot.
This service subscribes to the system D-Bus signal
org.freedesktop.login1.Manager.PrepareForSleep and re-runs
z13-touchpad-apply every time the machine wakes up. After a short delay
(it also restores the display brightness to the kernel-reported value,
see z13_tools/brightness.py).

--self-test: run the apply step once and exit, without touching D-Bus
(used by `z13-touchpad-apply --watch-test`; a fake signal is never
delivered because GDBus filters by sender).

Requires PyGObject (gi) for the D-Bus subscription; exits cleanly if the
system bus is unreachable.
"""

import os
import subprocess
import sys

# 仓库根 = 向上找含 z13_tools 包的祖先目录（源码树与 AppImage 通用）。
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(REPO_ROOT, "z13_tools")):
    _parent = os.path.dirname(REPO_ROOT)
    if _parent == REPO_ROOT:
        break
    REPO_ROOT = _parent
APPLY_SCRIPT = os.path.join(REPO_ROOT, "bin", "z13-touchpad-apply")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def run_apply():
    try:
        subprocess.run([APPLY_SCRIPT], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _restore_brightness():
    """Restore the brightness to the kernel-reported value after wake.

    brightness.py is optional (created by a parallel module); any failure
    here only warns on stderr and never crashes the watcher.
    """
    try:
        from z13_tools.brightness import get_brightness, set_brightness
    except ImportError as e:
        print(f"无法导入 brightness 模块，跳过亮度恢复: {e}", file=sys.stderr)
        return 0
    try:
        value = get_brightness()
        if value is None:
            print("未取得当前亮度，跳过亮度恢复", file=sys.stderr)
            return 0
        set_brightness(value)
    except Exception as e:
        print(f"亮度恢复失败: {e}", file=sys.stderr)
    return 0


def _schedule_brightness_restore():
    """Wait ~1.5s for the panel to settle, then restore brightness.

    Uses a GLib timeout so the D-Bus main loop is not blocked.
    """
    try:
        from gi.repository import GLib
    except ImportError as e:
        print(f"无法导入 GLib，跳过亮度恢复: {e}", file=sys.stderr)
        return
    GLib.timeout_add(1500, _restore_brightness)


def _on_prepare_for_sleep(_conn, _sender, _path, _iface, _signal, params):
    # PyGObject passes the user_data argument only when signal_subscribe() was
    # given one; this subscription is not, so the callback takes six arguments.
    # PrepareForSleep(True) -> going to sleep; PrepareForSleep(False) -> resumed.
    if params is not None and params.get_type_string() == "(b)":
        sleeping = params.get_child_value(0).get_boolean()
        if not sleeping:
            run_apply()
            _schedule_brightness_restore()


def main():
    if "--self-test" in sys.argv[1:]:
        run_apply()
        return 0

    try:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as e:
        print(f"缺少 PyGObject，无法监听休眠事件: {e}", file=sys.stderr)
        return 1

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as e:
        print(f"无法连接系统 D-Bus: {e}", file=sys.stderr)
        return 1

    bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                         "PrepareForSleep", "/org/freedesktop/login1",
                         None, Gio.DBusSignalFlags.NONE, _on_prepare_for_sleep)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
