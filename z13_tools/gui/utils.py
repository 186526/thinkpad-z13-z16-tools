#!/usr/bin/env python3
"""gui.py 的非 GTK 支撑逻辑：常量、配置读写、systemd 自启动、配套工具。

纯 Python 3 标准库（+ haptic），无 PyGObject 依赖，便于单元测试；
gui.py 通过 ``from gui_utils import *`` 重导出全部名字，保持
tests/test_gui_e2e.py 对 gui 模块级名字的 monkeypatch 兼容。

注意：load_config / save_config 接受显式 ``path`` 参数。gui.py 的包装层
会把 ``gui.CONFIG_FILE``（测试里被 monkeypatch 到临时路径）传进来，
避免函数体读到自己模块的模块级路径。
"""

import ast
import json
import os
import shutil
import subprocess
import sys

from z13_tools import haptic

# 通过 from gui_utils import * 重导出到 gui.py 的公共名字。
# load_config / save_config 会被 gui.py 的包装层覆盖（传入显式 path）。
__all__ = [
    "CONFIG_DIR", "CONFIG_FILE",
    "DEFAULT_INTENSITY", "INTENSITY_STEP",
    "ZONE_MAP", "PROFILE_LABELS",
    "ZONE_PRESETS", "ZONE_PRESET_LABELS",
    "EXT_UUID", "EXT_DIR", "EXT_INSTALL_SCRIPT",
    "CAMERA_SCRIPT", "PROBE_SCRIPT", "GPU_RESET_LOG",
    "FEATURE_ROWS",
    "ext_installed", "ext_enabled",
    "install_extension", "uninstall_extension",
    "probe_cameras", "gpu_reset_summary", "run_hw_probe",
    "fmt_feature_value", "fmt_bank_hex",
    "load_config", "save_config",
    "systemctl", "autostart_enabled", "set_autostart",
]

# 仓库根 = z13_tools/gui/ 的上两级；源码树与 AppImage 内（usr/share/
# z13-tools/）结构一致，脚本/监控/扩展统一从这里派生。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # z13_tools/gui
REPO_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
APPLY_SCRIPT = os.path.join(REPO_ROOT, "bin", "z13-touchpad-apply")

CONFIG_DIR = os.path.expanduser("~/.config/z13-g2-tools")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

UNIT_NAME = "z13-touchpad-haptic.service"
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
UNIT_FILE = os.path.join(UNIT_DIR, UNIT_NAME)
UNIT_RESUME = "z13-touchpad-resume.service"
UNIT_RESUME_FILE = os.path.join(UNIT_DIR, UNIT_RESUME)
RESUME_WATCH_SCRIPT = os.path.join(REPO_ROOT, "monitors", "resume-watch.py")

_UNIT_APPLY = (
    "[Unit]\n"
    "Description=Apply ThinkPad Z13/Z16 Gen 2 haptic touchpad settings\n"
    "After=graphical-session.target\n"
    "PartOf=graphical-session.target\n"
    "\n"
    "[Service]\n"
    "Type=oneshot\n"
    "ExecStart={script}\n"
    "\n"
    "[Install]\n"
    "WantedBy=graphical-session.target\n"
)

_UNIT_RESUME = (
    "[Unit]\n"
    "Description=Re-apply ThinkPad Z13 touchpad settings after suspend\n"
    "After=graphical-session.target\n"
    "PartOf=graphical-session.target\n"
    "\n"
    "[Service]\n"
    "Type=simple\n"
    "ExecStart=/usr/bin/env python3 {watch}\n"
    "\n"
    "[Install]\n"
    "WantedBy=graphical-session.target\n"
)

DEFAULT_INTENSITY = 50

# 触感强度档位：Windows 驱动为 0/25/50/75/100 五档，滑块按 25 吸附
INTENSITY_STEP = 25

# 触控板图上的按键顺序（视觉左→右）：(键标题, 点击键, 释放键)
ZONE_MAP = (
    ("左键", "zone_left", "zone_left_release"),
    ("中键", "zone_middle", "zone_middle_release"),
    ("右键", "zone_right", "zone_right_release"),
)

# 预设档位显示名（键与 haptic.PROFILES 一致）
PROFILE_LABELS = {"light": "轻触", "standard": "标准", "heavy": "重按"}

# 顶部按键预设力度（克数）：release 取 click 的 65% 附近，标准档即出厂默认
ZONE_PRESETS = {
    "light": {"click": 55, "release": 36},
    "standard": {"click": 76, "release": 50},
    "heavy": {"click": 110, "release": 72},
}
ZONE_PRESET_LABELS = {"light": "轻触", "standard": "标准", "heavy": "重按"}

# ---------------- 配套工具（GNOME 扩展 / 亮度 / 相机 / GPU reset / 硬件探测） ----------------

EXT_UUID = "z13-touchpad-quick@user"
EXT_DIR = os.path.expanduser(f"~/.local/share/gnome-shell/extensions/{EXT_UUID}")
EXT_INSTALL_SCRIPT = os.path.join(REPO_ROOT, "extensions", "install.sh")
CAMERA_SCRIPT = os.path.join(REPO_ROOT, "bin", "z13-camera-tool")
PROBE_SCRIPT = os.path.join(REPO_ROOT, "bin", "z13-hw-probe")
GPU_RESET_LOG = os.path.join(CONFIG_DIR, "gpu-resets.log")


def ext_installed():
    """GNOME 快速设置扩展是否已安装（目录存在即视为安装过）。"""
    return os.path.isdir(EXT_DIR)


def ext_enabled():
    """扩展是否在 GNOME 的启用列表里。"""
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        if out.startswith("["):
            return EXT_UUID in ast.literal_eval(out)
    except Exception:
        pass
    return False


def install_extension():
    """运行 extensions/install.sh（幂等，可重装）。返回 (ok, 消息)。"""
    if not os.path.exists(EXT_INSTALL_SCRIPT):
        return False, "找不到 extensions/install.sh"
    try:
        res = subprocess.run(["bash", EXT_INSTALL_SCRIPT],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"安装失败: {e}"
    tail = (res.stdout or res.stderr or "").strip().splitlines()
    msg = tail[-1] if tail else ("已安装" if res.returncode == 0 else "安装失败")
    return res.returncode == 0, msg


def uninstall_extension():
    """卸载扩展：从 GNOME 启用列表移除并删除扩展目录。返回 (ok, 消息)。"""
    removed = False
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        if out.startswith("["):
            enabled = ast.literal_eval(out)
            if EXT_UUID in enabled:
                enabled.remove(EXT_UUID)
                subprocess.run(
                    ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                     str(enabled)], check=True, timeout=3)
                removed = True
    except Exception:
        pass
    if os.path.isdir(EXT_DIR):
        shutil.rmtree(EXT_DIR, ignore_errors=True)
    return True, "已卸载扩展" + ("并移除启用项" if removed else "")


def probe_cameras():
    """运行 z13-camera-tool --json，返回相机清单文本；失败返回 None。"""
    try:
        res = subprocess.run([sys.executable, CAMERA_SCRIPT, "--json"],
                             capture_output=True, text=True, timeout=10)
        data = json.loads(res.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not data:
        return "未检测到相机"
    return " · ".join(f"{c.get('name', '?')} ({'IR' if c.get('ir') else 'RGB'})"
                      for c in data)


def gpu_reset_summary():
    """最近一次 GPU 重置记录摘要；无记录/不可读返回提示文本。"""
    if not os.path.exists(GPU_RESET_LOG):
        return "无重置记录"
    try:
        with open(GPU_RESET_LOG, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return "无法读取日志"
    return f"最近: {lines[-1]}" if lines else "日志为空"


def run_hw_probe():
    """运行 hw-probe.py（指纹/TPM/相机探测）。返回 (ok, 消息)。"""
    try:
        res = subprocess.run([sys.executable, PROBE_SCRIPT],
                             capture_output=True, text=True, timeout=30)
        lines = [ln.strip() for ln in (res.stdout or "").splitlines()
                 if "已写入" in ln]
        msg = lines[-1] if lines else "探测完成"
        return res.returncode == 0, msg
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"探测失败: {e}"

# Human-readable description of each known feature report.
# (report_id, data_length, title, note)
FEATURE_ROWS = (
    (3, 1, "报告 3 · 厂商参数", "Vendor 0xFF00:0x01"),
    (4, 1, "报告 4 · Inputmode", "输入模式 (0-10)"),
    (6, 1, "报告 6 · Surface / Button Switch", "表面开关 / 按钮开关，各 1 bit"),
    (7, 256, "报告 7 · 厂商参数区", "Vendor 0xFF00:0xC5 · 256 字节参数区"),
    (8, 1, "报告 8 · Contact Max / Button Type", "最大触点 / 按钮类型，各 4 bit"),
    (10, 1, "报告 10 · 厂商参数", "Digitizer Vendor 0x60，1 bit"),
    (11, 1, "报告 11 · Haptic Intensity", "触感强度 (0-100)"),
    (12, 1, "报告 12 · 厂商参数", "Vendor 0xFF00:0x01"),
)


def fmt_feature_value(rid, data):
    """Human-readable rendering of a single report's data bytes."""
    if not data:
        return "读取失败"
    b = data[0]
    if rid == 6:
        return f"Surface={b & 1}  Button={(b >> 1) & 1}  0x{b:02x}"
    if rid == 8:
        return f"Contact Max={b & 0x0F}  Button Type={(b >> 4) & 0x0F}  0x{b:02x}"
    if rid == 7:
        nz = haptic.bank_nonzero(data)
        return "全部为 0" if not nz else ", ".join(f"{i:x}={v}" for i, v in nz[:8])
    return f"0x{b:02x} ({b})"


def fmt_bank_hex(data):
    """16-column hex dump of the 256-byte report-7 bank."""
    lines = []
    for off in range(0, 256, 16):
        chunk = data[off:off + 16]
        lines.append(f"{off:03x}: " + " ".join(f"{b:02x}" for b in chunk))
    return "\n".join(lines)


def load_config(path=None):
    """读取配置文件；path 为 None 时用模块级 CONFIG_FILE。"""
    path = path or CONFIG_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(path=None, **values):
    """增量写配置文件；path 为 None 时用模块级 CONFIG_FILE。"""
    path = path or CONFIG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = load_config(path)
    cfg.update(values)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def systemctl(args):
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def autostart_enabled():
    """True when the login-time service is enabled and points at this repo."""
    r = systemctl(["is-enabled", UNIT_NAME])
    if r.returncode != 0 or "enabled" not in r.stdout:
        return False
    try:
        with open(UNIT_FILE, encoding="utf-8") as f:
            return APPLY_SCRIPT in f.read()
    except OSError:
        return False


def set_autostart(enabled):
    """Enable/disable the login-time apply + resume-watch services.

    Returns an error string, or None on success.
    """
    if enabled:
        try:
            os.makedirs(UNIT_DIR, exist_ok=True)
            with open(UNIT_FILE, "w", encoding="utf-8") as f:
                f.write(_UNIT_APPLY.format(script=APPLY_SCRIPT))
            with open(UNIT_RESUME_FILE, "w", encoding="utf-8") as f:
                f.write(_UNIT_RESUME.format(watch=RESUME_WATCH_SCRIPT))
        except OSError as e:
            return f"写入单元文件失败: {e}"
        r = systemctl(["daemon-reload"])
        if r.returncode != 0:
            return f"daemon-reload 失败: {r.stderr.strip()}"
        r = systemctl(["enable", UNIT_NAME, UNIT_RESUME])
        if r.returncode != 0:
            return f"启用服务失败: {r.stderr.strip()}"
        r = systemctl(["start", UNIT_RESUME])
        if r.returncode != 0:
            return f"启动唤醒监听失败: {r.stderr.strip()}"
    else:
        systemctl(["stop", UNIT_RESUME])
        systemctl(["disable", UNIT_NAME, UNIT_RESUME])
        for p in (UNIT_FILE, UNIT_RESUME_FILE):
            try:
                os.remove(p)
            except OSError:
                pass
        r = systemctl(["daemon-reload"])
        if r.returncode != 0:
            return f"daemon-reload 失败: {r.stderr.strip()}"
    return None
