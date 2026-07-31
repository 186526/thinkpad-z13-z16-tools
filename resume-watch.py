#!/usr/bin/env python3
"""Re-apply the saved touchpad config after suspend / hibernate.

The touchpad's firmware registers are RAM-only and revert to defaults on
suspend, but the login-time z13-touchpad-haptic.service only covers boot.
This service subscribes to the system D-Bus signal
org.freedesktop.login1.Manager.PrepareForSleep and re-runs
z13-touchpad-apply every time the machine wakes up.

Requires PyGObject (gi) for the D-Bus subscription; exits cleanly if the
system bus is unreachable.
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPLY_SCRIPT = os.path.join(BASE_DIR, "z13-touchpad-apply")


def run_apply():
    try:
        subprocess.run([APPLY_SCRIPT], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _on_prepare_for_sleep(_conn, _sender, _path, _iface, _signal, params, _data):
    # PrepareForSleep(True) -> going to sleep; PrepareForSleep(False) -> resumed.
    if params is not None and params.get_type_string() == "(b)":
        sleeping = params.get_child_value(0).get_boolean()
        if not sleeping:
            run_apply()


def main():
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
