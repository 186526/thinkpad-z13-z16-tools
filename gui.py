#!/usr/bin/env python3
"""ThinkPad Z13/Z16 Gen 2 haptic touchpad settings GUI (GTK4 / libadwaita).

Three pages:
  * 设置     - adjust the haptic feedback intensity (0-100), the click /
               release thresholds of the main zone (with optional 65%
               release linkage and preset profiles), the per-zone click /
               release forces (left/middle/right, selected via a clickable
               visual touchpad map), and optionally re-apply everything at
               login via a systemd user service (the device resets to
               defaults on every reboot).
  * HID 功能 - read-only view of every HID feature report the touchpad
               exposes (read via /dev/hidraw* ioctls).
  * 设备     - read-only device info (path, HID_ID, HID_NAME, HID_PHYS,
               driver, kernel module) parsed from /sys/class/hidraw sysfs.
"""

import ast
import json
import math
import os
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, Gtk, Pango, PangoCairo,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import haptic  # noqa: E402
from haptic import TouchpadError  # noqa: E402
import brightness  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPLY_SCRIPT = os.path.join(BASE_DIR, "z13-touchpad-apply")

CONFIG_DIR = os.path.expanduser("~/.config/z13-g2-tools")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

UNIT_NAME = "z13-touchpad-haptic.service"
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
UNIT_FILE = os.path.join(UNIT_DIR, UNIT_NAME)
UNIT_RESUME = "z13-touchpad-resume.service"
UNIT_RESUME_FILE = os.path.join(UNIT_DIR, UNIT_RESUME)
RESUME_WATCH_SCRIPT = os.path.join(BASE_DIR, "resume-watch.py")

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
EXT_INSTALL_SCRIPT = os.path.join(BASE_DIR, "extensions", "install.sh")
CAMERA_SCRIPT = os.path.join(BASE_DIR, "z13-camera-tool")
PROBE_SCRIPT = os.path.join(BASE_DIR, "hw-probe.py")
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


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(**values):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cfg = load_config()
    cfg.update(values)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
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


class TouchpadMap(Gtk.DrawingArea):
    """可视化触控板：上沿 TrackPoint 三键条带（左/中/右）+ 主点击区。

    本机实测确认：0x0091-0x0096 三个 zone 对应触控板上沿一条横向条带里的
    三个虚拟 TrackPoint 按键（左键/中键/右键），而非表面纵向分区。条带高约
    20%，三键按 40:20:40 宽度分配（中键较窄）；条带以下为整板可点击的主
    点击区（力度 0x0038，仅展示）。分区边界为示意，纯绘制
    组件不持有设备状态：数值与选中态由外部通过 set_values / set_selected /
    set_main_force 驱动；点击条带内按键时回调 on_select(index)，由
    MainWindow 统一同步（_selected 不在图内直接修改）。
    """

    _STRIP_FRAC = 0.20                 # 顶部条带占板高比例
    _SPLITS = (0.0, 0.40, 0.60, 1.0)   # 三键宽度 40:20:40
    _LABELS = ("左键", "中键", "右键")
    _ACCENT_FALLBACK = (0.208, 0.518, 0.894)  # #3584e4

    def __init__(self, on_select):
        super().__init__()
        self._on_select = on_select
        self._forces = [0, 0, 0]
        self._releases = [0, 0, 0]
        self._main_force_g = 0
        self._selected = 0
        self._hovered = -1
        self.set_content_width(280)
        self.set_content_height(180)
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    # ---------------- 外部接口 ----------------

    def set_values(self, forces, releases):
        """设置三键（左,中,右）的点击/释放克数并重绘。"""
        self._forces = list(forces)
        self._releases = list(releases)
        self.queue_draw()

    def set_main_force(self, grams):
        """主点击区（整板点击力度 0x0038）的克数，仅展示。"""
        self._main_force_g = grams
        self.queue_draw()

    def set_selected(self, index):
        self._selected = index
        self.queue_draw()

    # ---------------- 事件 ----------------

    def _zone_at(self, x, y):
        """返回 (x,y) 命中的键下标，主点击区内返回 -1。"""
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return -1
        if y > h * self._STRIP_FRAC:
            return -1  # 主点击区不参与分区选择
        if x < self._SPLITS[1] * w:
            return 0
        if x < self._SPLITS[2] * w:
            return 1
        return 2

    def _on_pressed(self, _gesture, _n, x, y):
        zone = self._zone_at(x, y)
        if zone < 0:
            return
        # 不在此直接改选中态：选中态以 MainWindow.selected_zone 为唯一
        # 数据源，统一由回调触发 _set_selected_zone() 同步组合框/滑块/高亮，
        # 避免回调提前返回时图上高亮与其余控件脱节。
        if self._on_select is not None:
            self._on_select(zone)

    def _on_motion(self, _ctrl, x, y):
        zone = self._zone_at(x, y)
        if zone != self._hovered:
            self._hovered = zone
            self.queue_draw()
        self.set_cursor_from_name("pointer" if zone >= 0 else "default")

    def _on_leave(self, _ctrl):
        if self._hovered != -1:
            self._hovered = -1
            self.queue_draw()
        self.set_cursor(None)

    # ---------------- 绘制 ----------------

    def _accent(self):
        try:
            rgba = Adw.StyleManager.get_default().get_accent_color()
            return (rgba.red, rgba.green, rgba.blue)
        except Exception:
            return self._ACCENT_FALLBACK

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @staticmethod
    def _rounded_rect4(cr, x, y, w, h, r_tl, r_tr, r_bl, r_br):
        """四角半径可分别指定的圆角矩形路径（用于贴边角跟随底板圆角）。"""
        cr.new_sub_path()
        cr.arc(x + w - r_tr, y + r_tr, r_tr, -math.pi / 2, 0)
        cr.arc(x + w - r_br, y + h - r_br, r_br, 0, math.pi / 2)
        cr.arc(x + r_bl, y + h - r_bl, r_bl, math.pi / 2, math.pi)
        cr.arc(x + r_tl, y + r_tl, r_tl, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _area, cr, width, height):
        w, h = float(width), float(height)
        radius = min(16.0, h / 2)
        accent = self._accent()
        strip_h = h * self._STRIP_FRAC

        # 底板：微亮于背景的填充 + 细描边
        self._rounded_rect(cr, 0, 0, w, h, radius)
        cr.set_source_rgba(1, 1, 1, 0.03)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1.0)
        cr.stroke()

        # 顶部条带内三键填充（选中 accent 淡色，悬停白色 8%，裁切在圆角内）
        for i in range(3):
            x0 = self._SPLITS[i] * w
            x1 = self._SPLITS[i + 1] * w
            key_radius = min(8.0, strip_h / 2, (x1 - x0) / 2)
            cr.save()
            self._rounded_rect(cr, 0, 0, w, h, radius)
            cr.clip()
            self._rounded_rect(cr, x0, 0, x1 - x0, strip_h, key_radius)
            if i == self._selected:
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.15)
                cr.fill()
            elif i == self._hovered:
                cr.set_source_rgba(1, 1, 1, 0.08)
                cr.fill()
            cr.restore()

        # 条带与主区分隔线 + 键间分隔虚线
        cr.set_dash([4.0, 3.0], 0)
        cr.set_source_rgba(1, 1, 1, 0.15)
        cr.set_line_width(1.0)
        cr.move_to(2, strip_h)
        cr.line_to(w - 2, strip_h)
        cr.stroke()
        for i in (1, 2):
            lx = self._SPLITS[i] * w
            cr.move_to(lx, 2)
            cr.line_to(lx, strip_h - 2)
            cr.stroke()
        cr.set_dash([], 0)

        # 选中键描边（圆角；贴边角跟随底板圆角，避免被底板圆角裁切成直角）
        if self._selected >= 0:
            x0 = self._SPLITS[self._selected] * w
            x1 = self._SPLITS[self._selected + 1] * w
            kw = x1 - x0 - 2
            kh = strip_h - 2
            key_radius = min(8.0, kh / 2, kw / 2)
            r_tl = r_tr = key_radius
            if self._selected == 0:
                r_tl = radius
            elif self._selected == 2:
                r_tr = radius
            self._rounded_rect4(cr, x0 + 1, 1, kw, kh, r_tl, r_tr, key_radius, key_radius)
            cr.set_source_rgba(accent[0], accent[1], accent[2], 0.6)
            cr.set_line_width(2.0)
            cr.stroke()

        # 三键文字：标签 + 克数
        for i in range(3):
            self._draw_key_text(cr, i, w, strip_h, accent)

        # 主点击区文字
        self._draw_main_text(cr, w, h, strip_h)

    def _draw_key_text(self, cr, i, w, strip_h, accent):
        x0 = self._SPLITS[i] * w
        zw = (self._SPLITS[i + 1] - self._SPLITS[i]) * w
        cy = strip_h / 2

        fg = self.get_style_context().get_color()
        if i == self._selected:
            rgb, alpha = accent, 1.0
        elif i == self._hovered:
            rgb, alpha = (fg.red, fg.green, fg.blue), 0.75
        else:
            rgb, alpha = (fg.red, fg.green, fg.blue), 0.55

        layout = self._create_layout(self._LABELS[i], 11)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_width(max(1, int(zw * Pango.SCALE)))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _lw, lh = layout.get_pixel_size()
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        cr.move_to(x0, cy - lh - 2)
        PangoCairo.show_layout(cr, layout)

        value_layout = self._create_layout(
            f"{self._forces[i]}g / {self._releases[i]}g", 11,
            Pango.Weight.SEMIBOLD)
        value_layout.set_alignment(Pango.Alignment.CENTER)
        value_layout.set_width(max(1, int(zw * Pango.SCALE)))
        value_layout.set_ellipsize(Pango.EllipsizeMode.END)
        _tw, th = value_layout.get_pixel_size()
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        cr.move_to(x0, cy + 2)
        PangoCairo.show_layout(cr, value_layout)

    def _draw_main_text(self, cr, w, h, strip_h):
        text = (f"主点击区 · 点击力度 {self._main_force_g}g"
                if self._main_force_g else "主点击区 · 整板可点击")
        layout = self._create_layout(text, 11)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_width(max(1, int(w * Pango.SCALE)))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        _tw, th = layout.get_pixel_size()
        fg = self.get_style_context().get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.45)
        cr.move_to(0, (strip_h + h) / 2 - th / 2)
        PangoCairo.show_layout(cr, layout)

    def _create_layout(self, text, size_px, weight=Pango.Weight.NORMAL):
        """继承 widget 的 Pango 上下文/主题字体，仅覆盖大小与字重。

        用 create_pango_layout() 而非 PangoCairo.create_layout(cr)，这样
        字体族（GNOME 全局字体，如 Cantarell）与语言回退都跟随主题；
        像素大小用 set_absolute_size()，避免随屏幕分辨率跳变。
        """
        layout = self.create_pango_layout(text)
        font = self.get_pango_context().get_font_description().copy()
        font.set_absolute_size(size_px * Pango.SCALE)
        font.set_weight(weight)
        layout.set_font_description(font)
        return layout


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, developer=False):
        super().__init__(application=app, title="Z13/Z16 Gen 2 工具箱",
                         default_width=480, default_height=720)

        self.device = None
        self.current = None
        self._debounce_id = None
        self._apply_id = None
        self._syncing = False
        self._initializing = True
        self._developer = developer
        self._dev_pages_added = False
        # 异步写入串行队列：设备 ioctl 在后台线程执行，避免 UI 卡顿
        self._applying = False
        self._pending_work = None
        self._pending_on_ok = None
        # 配套工具状态
        self._brightness_value = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text="重新检测并刷新")
        self.refresh_button.connect("clicked", self._on_refresh)
        header.pack_end(self.refresh_button)

        # 菜单：开发者信息开关（HID 功能 / 设备页对普通用户隐藏）
        menu = Gio.Menu()
        dev_item = Gio.MenuItem()
        dev_item.set_label("开发者信息")
        dev_item.set_attribute_value("toggle", GLib.Variant.new_boolean(True))
        dev_item.set_detailed_action("app.developer-mode")
        menu.append_item(dev_item)
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                          tooltip_text="菜单")
        self.menu_button.set_menu_model(menu)
        header.pack_end(self.menu_button)

        title_label = Gtk.Label(label="Z13/Z16 Gen 2 工具箱")
        title_label.add_css_class("title")
        header.set_title_widget(title_label)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_titled(self._build_settings_page(), "settings", "设置")
        # HID 功能 / 设备页仅在开发者模式下加入（单页时切换器自动隐藏）
        self._features_page = self._build_features_page()
        self._device_page = self._build_device_page()
        if developer:
            self._apply_developer_mode(True)
        toolbar.add_top_bar(header)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)
        toolbar.set_content(self.toast_overlay)

        # 底部状态条：设备写入时显示 spinner 进度反馈
        self.apply_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.apply_bar.set_margin_top(6)
        self.apply_bar.set_margin_bottom(6)
        self.apply_bar.set_halign(Gtk.Align.CENTER)
        self.apply_spinner = Adw.Spinner()
        self.apply_label = Gtk.Label(label="正在应用设置…")
        self.apply_label.add_css_class("dim-label")
        self.apply_bar.append(self.apply_spinner)
        self.apply_bar.append(self.apply_label)
        self.apply_bar.set_visible(False)
        toolbar.add_bottom_bar(self.apply_bar)

        self.set_content(toolbar)

        self._load_state()
        self._initializing = False

    def _apply_developer_mode(self, enabled):
        """开发者模式：把 HID 功能 / 设备页加入或移出 Stack。"""
        if enabled and not self._dev_pages_added:
            self.stack.add_titled(self._features_page, "features", "HID 功能")
            self.stack.add_titled(self._device_page, "device", "设备")
            self._dev_pages_added = True
            if self.device is not None:
                self._refresh_features()
        elif not enabled and self._dev_pages_added:
            self.stack.remove(self._features_page)
            self.stack.remove(self._device_page)
            self._dev_pages_added = False

    # ---------------- settings page ----------------

    def _build_settings_page(self):
        page = Adw.PreferencesPage()

        dev_group = Adw.PreferencesGroup(title="触控板")
        self.device_row = Adw.ActionRow(title="Sensel 触控板", subtitle="正在检测…")
        dev_group.add(self.device_row)
        page.add(dev_group)

        click_group = Adw.PreferencesGroup(
            title="点击力度",
            description="调低后轻按即可点击；调高后需要更用力。")
        self.click_label = Gtk.Label(label="点击 164g", width_chars=10, xalign=1.0)
        self.click_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                    10, 500, 1)
        self.click_scale.set_hexpand(True)
        self.click_scale.set_draw_value(False)
        self.click_scale.connect("value-changed", self._on_click_changed)
        click_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        click_box.set_margin_top(8)
        click_box.set_margin_bottom(8)
        click_box.set_margin_start(16)
        click_box.set_margin_end(16)
        click_box.append(self.click_label)
        click_box.append(self.click_scale)

        self.release_label = Gtk.Label(label="释放 108g", width_chars=10, xalign=1.0)
        self.release_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                      10, 500, 1)
        self.release_scale.set_hexpand(True)
        self.release_scale.set_draw_value(False)
        self.release_scale.connect("value-changed", self._on_release_changed)
        release_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        release_box.set_margin_top(8)
        release_box.set_margin_bottom(8)
        release_box.set_margin_start(16)
        release_box.set_margin_end(16)
        release_box.append(self.release_label)
        release_box.append(self.release_scale)

        self.link_switch = Adw.SwitchRow(
            title="释放力度自动跟随",
            subtitle="保持点击和松开时的力度比例自然（推荐）")
        self.link_switch.set_active(True)
        self.link_switch.connect("notify::active", self._on_link_toggled)

        self.profile_row = Adw.ComboRow(
            title="预设档位",
            subtitle="快速选择适合的按压力度，选择后立即应用")
        self.profile_model = Gtk.StringList.new(["轻触", "标准", "重按"])
        self.profile_row.set_model(self.profile_model)
        self.profile_row.connect("notify::selected", self._on_profile_selected)

        click_group.add(self.profile_row)
        click_group.add(click_box)
        click_group.add(release_box)
        click_group.add(self.link_switch)
        page.add(click_group)

        zone_group = Adw.PreferencesGroup(
            title="顶部按键力度",
            description="分别调整触控板上沿左、中、右三个按键的力度。"
                        "点击图中的按键即可选择。")
        self.zone_values = {}   # key -> 克数
        for _ztitle, click_key, release_key in ZONE_MAP:
            self.zone_values[click_key] = 0
            self.zone_values[release_key] = 0
        self.selected_zone = 0

        # 一键套用三键预设力度
        self.zone_profile_row = Adw.ComboRow(
            title="按键预设",
            subtitle="一键设置左、中、右三个按键的按压力度")
        self.zone_profile_model = Gtk.StringList.new(
            [ZONE_PRESET_LABELS[n] for n in ZONE_PRESETS])
        self.zone_profile_row.set_model(self.zone_profile_model)
        self.zone_profile_row.connect("notify::selected",
                                      self._on_zone_profile_selected)
        zone_group.add(self.zone_profile_row)

        touchpad_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        touchpad_wrap.set_halign(Gtk.Align.CENTER)
        touchpad_wrap.set_margin_top(12)
        touchpad_wrap.set_margin_bottom(8)
        self.touchpad_map = TouchpadMap(on_select=self._on_zone_pick)
        touchpad_wrap.append(self.touchpad_map)
        zone_group.add(touchpad_wrap)

        self.zone_combo = Adw.ComboRow(
            title="要调整的按键",
            subtitle="点击上方图形或在此选择要调节的按键")
        self.zone_combo_model = Gtk.StringList.new(
            [ztitle for ztitle, _ck, _rk in ZONE_MAP])
        self.zone_combo.set_model(self.zone_combo_model)
        self.zone_combo.set_selected(0)
        self.zone_combo.connect("notify::selected", self._on_zone_combo_changed)
        zone_group.add(self.zone_combo)

        self.zone_click_label = Gtk.Label(label="点击 —", width_chars=10, xalign=1.0)
        self.zone_click_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                         10, 500, 1)
        self.zone_click_scale.set_hexpand(True)
        self.zone_click_scale.set_draw_value(False)
        self.zone_click_scale.connect("value-changed", self._on_zone_changed)
        zone_click_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        zone_click_box.set_margin_top(8)
        zone_click_box.set_margin_bottom(8)
        zone_click_box.set_margin_start(16)
        zone_click_box.set_margin_end(16)
        zone_click_box.append(self.zone_click_label)
        zone_click_box.append(self.zone_click_scale)
        zone_group.add(zone_click_box)

        self.zone_release_label = Gtk.Label(label="释放 —", width_chars=10, xalign=1.0)
        self.zone_release_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                           10, 500, 1)
        self.zone_release_scale.set_hexpand(True)
        self.zone_release_scale.set_draw_value(False)
        self.zone_release_scale.connect("value-changed", self._on_zone_changed)
        zone_release_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        zone_release_box.set_margin_top(8)
        zone_release_box.set_margin_bottom(8)
        zone_release_box.set_margin_start(16)
        zone_release_box.set_margin_end(16)
        zone_release_box.append(self.zone_release_label)
        zone_release_box.append(self.zone_release_scale)
        zone_group.add(zone_release_box)
        page.add(zone_group)

        haptic_group = Adw.PreferencesGroup(
            title="触感强度",
            description="调整按下触控板时的振动反馈。")
        self.value_label = Gtk.Label(label=str(DEFAULT_INTENSITY), width_chars=3,
                                     xalign=1.0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                              0, 100, INTENSITY_STEP)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        self.scale.connect("value-changed", self._on_value_changed)

        slider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        slider_box.set_margin_top(8)
        slider_box.set_margin_bottom(8)
        slider_box.set_margin_start(16)
        slider_box.set_margin_end(16)
        slider_box.append(self.value_label)
        slider_box.append(self.scale)

        self.reset_button = Gtk.Button(label="恢复默认")
        self.reset_button.connect("clicked", self._on_reset)

        haptic_group.add(slider_box)
        haptic_group.add(self.reset_button)
        page.add(haptic_group)

        auto_group = Adw.PreferencesGroup(
            title="自动恢复设置",
            description="设备重启或从睡眠唤醒后会恢复默认值。")
        self.autostart_switch = Adw.SwitchRow(title="登录和唤醒后重新应用",
                                              subtitle="自动恢复当前保存的触控板设置")
        self.autostart_switch.connect("notify::active", self._on_autostart_toggled)
        auto_group.add(self.autostart_switch)
        page.add(auto_group)

        companion_group = Adw.PreferencesGroup(
            title="配套工具",
            description="GNOME 快速设置开关、OLED 亮度等配套功能的一键入口。")
        # GNOME 扩展：未安装 -> 安装；已安装 -> 卸载
        self.ext_row = Adw.ActionRow(title="GNOME 快速设置开关",
                                     subtitle="未检测")
        self.ext_button = Gtk.Button(label="安装")
        self.ext_button.add_css_class("suggested-action")
        self.ext_button.connect("clicked", self._on_ext_toggle)
        self.ext_row.add_suffix(self.ext_button)
        companion_group.add(self.ext_row)

        # OLED 亮度
        self.brightness_row = Adw.ActionRow(title="OLED 亮度",
                                            subtitle="不可用（权限或设备缺失）")
        self.brightness_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                         0, 100, 1)
        self.brightness_scale.set_hexpand(True)
        self.brightness_scale.set_draw_value(False)
        self.brightness_scale.set_sensitive(False)
        self.brightness_scale.connect("value-changed", self._on_brightness_changed)
        brightness_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brightness_box.set_margin_top(8)
        brightness_box.set_margin_bottom(8)
        brightness_box.set_margin_start(16)
        brightness_box.set_margin_end(16)
        brightness_box.append(self.brightness_scale)
        companion_group.add(self.brightness_row)
        companion_group.add(brightness_box)

        # 相机检测
        self.camera_row = Adw.ActionRow(title="相机检测", subtitle="未检测")
        self.camera_button = Gtk.Button(label="重新检测")
        self.camera_button.connect("clicked", self._on_camera_probe)
        self.camera_row.add_suffix(self.camera_button)
        companion_group.add(self.camera_row)

        # GPU reset 监控（常驻监听由 systemd 用户服务运行，这里只读展示）
        self.gpu_row = Adw.ActionRow(title="GPU 重置监控",
                                     subtitle="无重置记录")
        companion_group.add(self.gpu_row)

        # 硬件探测
        self.probe_row = Adw.ActionRow(title="硬件探测",
                                       subtitle="指纹 / TPM / 相机")
        self.probe_button = Gtk.Button(label="运行")
        self.probe_button.connect("clicked", self._on_hw_probe)
        self.probe_row.add_suffix(self.probe_button)
        companion_group.add(self.probe_row)
        page.add(companion_group)

        reset_group = Adw.PreferencesGroup(
            description="把所有力度与触感设置恢复为出厂默认"
                        "（点击 164g / 释放 108g / 分区 76g / 触感 50%）。")
        self.reset_all_button = Gtk.Button(label="恢复出厂设置")
        self.reset_all_button.add_css_class("destructive-action")
        self.reset_all_button.set_halign(Gtk.Align.CENTER)
        self.reset_all_button.set_margin_top(8)
        self.reset_all_button.set_margin_bottom(8)
        self.reset_all_button.connect("clicked", self._on_reset_all)
        reset_group.add(self.reset_all_button)
        page.add(reset_group)
        return page

    # ---------------- HID features page ----------------

    def _build_features_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="特性报告 (Feature Reports)",
            description="设备通过 HID feature 报告暴露的全部配置项（只读）。"
                        "实验性写入工具见 feature-probe.py。")
        self._feature_rows = {}
        self._bank_expander = None
        self._bank_label = None
        for rid, size, title, note in FEATURE_ROWS:
            if rid == 7:
                row = Adw.ExpanderRow(title=title, subtitle=note)
                self._bank_label = Gtk.Label(label="", selectable=True)
                self._bank_label.set_halign(Gtk.Align.FILL)
                self._bank_label.add_css_class("monospace")
                self._bank_label.set_margin_top(4)
                self._bank_label.set_margin_bottom(4)
                self._bank_label.set_margin_start(8)
                self._bank_label.set_margin_end(8)
                row.add_row(self._bank_label)
                self._bank_expander = row
            else:
                row = Adw.ActionRow(title=title, subtitle=note)
            group.add(row)
            self._feature_rows[rid] = row
        page.add(group)

        reg_group = Adw.PreferencesGroup(
            title="固件寄存器 (Register Pipe)",
            description="点击力度等参数通过 report 0x09 厂商管道读写，"
                        "值为内存态，重启/休眠后恢复默认（只读展示）。")
        self._reg_rows = {}
        for reg in haptic.REGISTERS:
            row = Adw.ActionRow(title=f"0x{reg.addr:04X} · {reg.name}",
                                subtitle=f"默认 {reg.fmt(reg.default)}")
            reg_group.add(row)
            self._reg_rows[reg.key] = row
        page.add(reg_group)

        tip = Adw.PreferencesGroup(
            title="实验",
            description="点击灵敏度（按压力阈值）= 固件寄存器 0x0038，已在「设置」页"
                        "提供滑块。参考逆向项目：sensel-touchpad-linux / cirque-fix。")
        page.add(tip)
        return page

    # ---------------- device page ----------------

    def _build_device_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="设备信息",
            description="从 /sys/class/hidraw/*/device/uevent 读取（只读）")
        self._device_rows = {}
        for title in ("设备路径", "HID_ID", "HID_NAME", "HID_PHYS", "驱动", "内核模块"):
            row = Adw.ActionRow(title=title, subtitle="—")
            group.add(row)
            self._device_rows[title] = row
        footer = Gtk.Label(
            label="Sensel 触控板 · 参考 Arch Wiki: Lenovo_ThinkPad_Z13/Z16_Gen_2")
        footer.set_halign(Gtk.Align.CENTER)
        footer.add_css_class("dim-label")
        footer_group = Adw.PreferencesGroup()
        footer_group.add(footer)
        page.add(footer_group)
        return page

    # ---------------- state ----------------

    def _load_state(self):
        self.device = haptic.find_device()
        if self.device is None:
            self.device_row.set_subtitle("未检测到支持的触控板")
            self.device_row.add_css_class("error")
            for row in self._device_rows.values():
                row.set_subtitle("未检测到设备")
            self._set_controls_enabled(False)
            self._refresh_companion()
            return

        self.device_row.set_subtitle("已连接")
        info = haptic.device_info(self.device) or {}
        self._device_rows["设备路径"].set_subtitle(self.device)
        self._device_rows["HID_ID"].set_subtitle(info.get("HID_ID", "—") or "—")
        self._device_rows["HID_NAME"].set_subtitle(info.get("HID_NAME", "—") or "—")
        self._device_rows["HID_PHYS"].set_subtitle(info.get("HID_PHYS", "—") or "—")
        self._device_rows["驱动"].set_subtitle(info.get("DRIVER", "—") or "—")
        self._device_rows["内核模块"].set_subtitle(info.get("MODULE") or "未知")
        try:
            self.current = haptic.get_intensity(self.device)
            if self._developer:
                self._refresh_features()
            regs = haptic.read_all_registers(self.device)
        except TouchpadError as e:
            self.device_row.set_subtitle("无法访问触控板，请检查设备权限")
            self._set_controls_enabled(False)
            self._refresh_companion()
            return

        click = regs.get("click_force")
        if click is not None:
            self.click_scale.set_value(click * 2)
            self.click_label.set_text(f"点击 {click * 2}g")
            self.touchpad_map.set_main_force(click * 2)
            # 选中与当前点击力度最接近的预设档（回填，不触发应用）
            names = list(haptic.PROFILES)
            best = min(names, key=lambda n: abs(
                haptic.PROFILES[n]["click_force"] - click * 2))
            self.profile_row.set_selected(names.index(best))
        release = regs.get("click_release")
        if release is not None:
            self.release_scale.set_value(release * 2)
            self.release_label.set_text(f"释放 {release * 2}g")

        for key in self.zone_values:
            raw = regs.get(key)
            if raw is not None:
                self.zone_values[key] = raw * 2
        # 统一入口：同时刷新 combo/高亮/滑块/图上数值，修复选中态分歧
        self._set_selected_zone(self.selected_zone)

        snapped = int(round(self.current / INTENSITY_STEP) * INTENSITY_STEP)
        self.scale.set_value(snapped)
        self.value_label.set_text(str(snapped))
        self._set_controls_enabled(True)
        self.autostart_switch.set_active(autostart_enabled())
        self._refresh_companion()

    def _set_controls_enabled(self, enabled):
        for w in (self.scale, self.reset_button, self.autostart_switch,
                  self.click_scale, self.release_scale, self.link_switch,
                  self.profile_row, self.zone_profile_row,
                  self.zone_click_scale, self.zone_release_scale,
                  self.zone_combo, self.touchpad_map,
                  self.reset_all_button):
            w.set_sensitive(enabled)

    def _refresh_features(self):
        try:
            features = haptic.read_features(self.device)
            regs = haptic.read_all_registers(self.device)
        except TouchpadError:
            return
        for rid, _size, title, note in FEATURE_ROWS:
            row = self._feature_rows[rid]
            if rid == 7:
                data = features.get(rid)
                subtitle = note
                if data is not None:
                    nz = haptic.bank_nonzero(data)
                    summary = "全部为 0" if not nz else \
                        ", ".join(f"{i:x}={v}" for i, v in nz[:8])
                    subtitle = f"{note} · 当前: {summary}"
                    self._bank_label.set_text(fmt_bank_hex(data))
                row.set_subtitle(subtitle)
            else:
                data = features.get(rid)
                value = fmt_feature_value(rid, data) if data is not None else "读取失败"
                row.set_subtitle(f"{note} · 当前: {value}")
        for reg in haptic.REGISTERS:
            row = self._reg_rows[reg.key]
            raw = regs.get(reg.key)
            if raw is None:
                row.set_subtitle("读取失败")
                continue
            marker = "" if raw == reg.default else "（已修改）"
            row.set_subtitle(f"当前 {reg.fmt(raw)}{marker} · 默认 {reg.fmt(reg.default)}")

    def _on_refresh(self, _btn):
        self._initializing = True
        self._load_state()
        self._initializing = False

    # ---------------- intensity ----------------

    def _on_value_changed(self, scale):
        value = int(round(scale.get_value() / INTENSITY_STEP) * INTENSITY_STEP)
        self.value_label.set_text(str(value))
        if self._initializing or self.device is None:
            return
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(400, self._apply)

    def _apply(self):
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = None
        if self.device is None:
            return GLib.SOURCE_REMOVE
        value = int(round(self.scale.get_value() / INTENSITY_STEP) * INTENSITY_STEP)
        # 回填时屏蔽回调，避免 set_value 触发 value-changed 再排一次防抖
        hid = self.scale.handler_block_by_func(self._on_value_changed)
        try:
            self.scale.set_value(value)
        finally:
            self.scale.handler_unblock_by_func(self._on_value_changed)
        self.value_label.set_text(str(value))

        def work():
            haptic.set_intensity(self.device, value)

        def on_ok(_result):
            self.current = value
            save_config(haptic_intensity=value)
            self._show_toast(f"已应用触感强度 {value}")

        self._enqueue_apply(work, on_ok)
        return GLib.SOURCE_REMOVE

    def _on_reset(self, _btn):
        self.scale.set_value(DEFAULT_INTENSITY)

    # ---------------- click force / release ----------------

    def _on_click_changed(self, scale):
        grams = int(round(scale.get_value()))
        self.click_label.set_text(f"点击 {grams}g")
        if self._initializing or self.device is None or self._syncing:
            return
        if self.link_switch.get_active():
            self._sync_release_from_click()
        self._schedule_apply()

    def _on_release_changed(self, scale):
        grams = int(round(scale.get_value()))
        self.release_label.set_text(f"释放 {grams}g")
        if self._initializing or self.device is None or self._syncing:
            return
        if not self.link_switch.get_active():
            self._schedule_apply()

    def _on_profile_selected(self, row, _pspec):
        if self._initializing or self.device is None:
            return
        idx = row.get_selected()
        names = list(haptic.PROFILES)
        if not 0 <= idx < len(names):
            return
        name = names[idx]
        try:
            raw_vals = {}
            for key, grams in haptic.PROFILES[name].items():
                reg = haptic.REGISTERS_BY_KEY[key]
                raw_vals[key] = reg.from_human(grams)
                haptic.write_register(self.device, reg.addr, raw_vals[key])
        except haptic.RegisterError as e:
            self._show_toast(str(e), is_error=True)
            return
        # 同步滑块到设备真实值并保存（与 _apply_all 相同的 raw*2 显示）
        self._syncing = True
        self.click_scale.set_value(raw_vals["click_force"] * 2)
        self.click_label.set_text(f"点击 {raw_vals['click_force'] * 2}g")
        self.release_scale.set_value(raw_vals["click_release"] * 2)
        self.release_label.set_text(f"释放 {raw_vals['click_release'] * 2}g")
        self.touchpad_map.set_main_force(raw_vals["click_force"] * 2)
        self._syncing = False
        save_config(**{key: raw_vals[key] * 2 for key in raw_vals})
        label = PROFILE_LABELS.get(name, name)
        self._show_toast(f"已应用预设「{label}」: 点击 {raw_vals['click_force'] * 2}g · "
                         f"释放 {raw_vals['click_release'] * 2}g")

    def _on_zone_changed(self, scale):
        # 程序化回填（_syncing）时不改写模型，避免回调环与误写
        if self._syncing:
            return
        grams = int(round(scale.get_value()))
        click_key, release_key = self._zone_keys_for_selected()
        if scale is self.zone_click_scale:
            self.zone_values[click_key] = grams
            self.zone_click_label.set_text(f"点击 {grams}g")
        else:
            self.zone_values[release_key] = grams
            self.zone_release_label.set_text(f"释放 {grams}g")
        self._refresh_zone_map()
        if self._initializing or self.device is None:
            return
        self._schedule_apply()

    def _zone_keys_for_selected(self):
        """当前选中分区对应的 (点击键, 释放键)。"""
        _ztitle, click_key, release_key = ZONE_MAP[self.selected_zone]
        return click_key, release_key

    def _refresh_zone_map(self):
        """把 zone_values 推给触控板图重绘（视觉顺序 左/中/右）。"""
        self.touchpad_map.set_values(
            [self.zone_values[ZONE_MAP[i][1]] for i in range(3)],
            [self.zone_values[ZONE_MAP[i][2]] for i in range(3)])

    def _sync_zone_sliders(self):
        """按当前选中分区回填两条滑块（_syncing 防抖，不触发应用）。"""
        click_key, release_key = self._zone_keys_for_selected()
        self._syncing = True
        self.zone_click_scale.set_value(self.zone_values[click_key])
        self.zone_click_label.set_text(f"点击 {self.zone_values[click_key]}g")
        self.zone_release_scale.set_value(self.zone_values[release_key])
        self.zone_release_label.set_text(f"释放 {self.zone_values[release_key]}g")
        self._syncing = False

    def _set_selected_zone(self, index):
        """切换选中分区：统一同步 combo、图上高亮、图上数值与两条滑块。

        MainWindow.selected_zone 是唯一选中态数据源，所有路径
        （图上点击 / 下拉选择 / 设备刷新）都从这里同步。
        """
        if not 0 <= index < len(ZONE_MAP):
            return
        self.selected_zone = index
        self.touchpad_map.set_selected(index)
        hid = self.zone_combo.handler_block_by_func(self._on_zone_combo_changed)
        try:
            self.zone_combo.set_selected(index)
        finally:
            self.zone_combo.handler_unblock_by_func(self._on_zone_combo_changed)
        self._sync_zone_sliders()
        self._refresh_zone_map()

    def _on_zone_pick(self, index):
        if self._initializing or self.device is None:
            return
        self._set_selected_zone(index)

    def _on_zone_combo_changed(self, row, _pspec):
        if self._initializing or self.device is None:
            return
        self._set_selected_zone(row.get_selected())

    def _on_zone_profile_selected(self, row, _pspec):
        """一键套用顶部三键预设力度（轻触/标准/重按）。"""
        if self._initializing:
            return
        index = row.get_selected()
        if not 0 <= index < len(ZONE_PRESETS):
            return
        name = list(ZONE_PRESETS)[index]
        preset = ZONE_PRESETS[name]
        for _ztitle, click_key, release_key in ZONE_MAP:
            self.zone_values[click_key] = preset["click"]
            self.zone_values[release_key] = preset["release"]
        self._sync_zone_sliders()
        self._refresh_zone_map()
        if self.device is None:
            return
        raws = {key: haptic.REGISTERS_BY_KEY[key].from_human(grams)
                for key, grams in self.zone_values.items()}

        def work():
            for key, raw in raws.items():
                reg = haptic.REGISTERS_BY_KEY[key]
                haptic.write_register(self.device, reg.addr, raw)

        def on_ok(_result):
            save_config(**{key: raw * 2 for key, raw in raws.items()})
            self._show_toast(f"已应用按键预设「{ZONE_PRESET_LABELS[name]}」")

        self._enqueue_apply(work, on_ok)

    def _on_link_toggled(self, switch, _pspec):
        if self._initializing:
            return
        if switch.get_active():
            self._sync_release_from_click()

    def _sync_release_from_click(self):
        """Release raw = 65% of the click raw (grams/2), like the reference tool."""
        click_g = int(round(self.click_scale.get_value()))
        click_raw = haptic.REGISTERS_BY_KEY["click_force"].from_human(click_g)
        rel_raw = max(1, round(click_raw * 0.65))
        self._syncing = True
        self.release_scale.set_value(rel_raw * 2)
        self.release_label.set_text(f"释放 {rel_raw * 2}g")
        self._syncing = False

    def _schedule_apply(self):
        if self._apply_id is not None:
            GLib.source_remove(self._apply_id)
        self._apply_id = GLib.timeout_add(400, self._apply_all)

    def _apply_all(self):
        self._apply_id = None
        if self.device is None:
            return GLib.SOURCE_REMOVE
        # 主线程收集最新值，换算成 raw
        click_g = int(round(self.click_scale.get_value()))
        click_raw = haptic.REGISTERS_BY_KEY["click_force"].from_human(click_g)
        if self.link_switch.get_active():
            rel_raw = max(1, round(click_raw * 0.65))
        else:
            rel_g = int(round(self.release_scale.get_value()))
            rel_raw = haptic.REGISTERS_BY_KEY["click_release"].from_human(rel_g)
        zone_raws = {key: haptic.REGISTERS_BY_KEY[key].from_human(grams)
                     for key, grams in self.zone_values.items()}
        values = {"click_force": click_raw, "click_release": rel_raw}
        values.update(zone_raws)

        def work():
            # 后台线程执行设备 ioctl（含 ACK 等待），避免 UI 卡顿
            for key, raw in values.items():
                reg = haptic.REGISTERS_BY_KEY[key]
                haptic.write_register(self.device, reg.addr, raw)

        def on_ok(_result):
            # 把滑块同步到设备真实值（raw*2），避免 165g -> raw 83 -> 166g 显示偏差
            self._syncing = True
            self.click_scale.set_value(click_raw * 2)
            self.click_label.set_text(f"点击 {click_raw * 2}g")
            self.touchpad_map.set_main_force(click_raw * 2)
            if self.link_switch.get_active():
                self.release_scale.set_value(rel_raw * 2)
                self.release_label.set_text(f"释放 {rel_raw * 2}g")
            for key, raw in zone_raws.items():
                self.zone_values[key] = raw * 2
            self._syncing = False
            self._sync_zone_sliders()
            self._refresh_zone_map()
            cfg = {"click_force": click_raw * 2, "click_release": rel_raw * 2}
            cfg.update({key: raw * 2 for key, raw in zone_raws.items()})
            save_config(**cfg)
            self._show_toast(f"点击力度 {click_raw * 2}g · 释放 {rel_raw * 2}g")

        self._enqueue_apply(work, on_ok)
        return GLib.SOURCE_REMOVE

    # ---------------- 异步写入队列 ----------------

    def _enqueue_apply(self, work, on_ok):
        """把一次设备写入放入串行队列；已有写入进行时合并为最新请求。"""
        self._pending_work = work
        self._pending_on_ok = on_ok
        self._kick_apply_queue()

    def _kick_apply_queue(self):
        if self._applying or self._pending_work is None:
            return
        work, on_ok = self._pending_work, self._pending_on_ok
        self._pending_work = None
        self._pending_on_ok = None
        self._applying = True
        self._show_applying(True)
        threading.Thread(target=self._apply_worker, args=(work, on_ok),
                         daemon=True).start()

    def _apply_worker(self, work, on_ok):
        try:
            work()
            GLib.idle_add(self._finish_apply_queue, True, None, on_ok)
        except (TouchpadError, haptic.RegisterError, ValueError) as e:
            GLib.idle_add(self._finish_apply_queue, False, str(e), on_ok)

    def _finish_apply_queue(self, ok, err, on_ok):
        self._applying = False
        self._show_applying(False)
        if ok:
            if on_ok is not None:
                on_ok(None)
        else:
            self._show_toast(err, is_error=True)
            self._load_state()  # 写失败：重读设备，恢复 UI 到真实值
        self._kick_apply_queue()

    def _show_applying(self, visible):
        """底部状态条：设备写入期间的进行中反馈（Adw.Spinner 可见即转）。"""
        self.apply_bar.set_visible(visible)

    # ---------------- autostart / factory reset ----------------

    def _on_autostart_toggled(self, switch, _pspec):
        if self._initializing:
            return
        enabled = switch.get_active()
        err = set_autostart(enabled)
        if err:
            self._show_toast(err, is_error=True)
            hid = switch.handler_block_by_func(self._on_autostart_toggled)
            try:
                switch.set_active(not enabled)
            finally:
                switch.handler_unblock_by_func(self._on_autostart_toggled)
        else:
            self._show_toast("已开启登录和唤醒时自动恢复" if enabled
                             else "已关闭登录和唤醒时自动恢复")

    def _on_reset_all(self, _btn):
        """一键恢复出厂：所有寄存器写回固件默认，并同步 UI 与配置。"""
        if self._initializing or self.device is None:
            return

        def work():
            for reg in haptic.REGISTERS:
                haptic.write_register(self.device, reg.addr, reg.default)

        def on_ok(_result):
            self._load_state()  # 重读设备，刷新全部滑块/图上数值
            cfg = {"haptic_intensity":
                   int(round(self.current / INTENSITY_STEP) * INTENSITY_STEP)}
            cfg.update(self.zone_values)
            cfg["click_force"] = int(round(self.click_scale.get_value()))
            cfg["click_release"] = int(round(self.release_scale.get_value()))
            save_config(**cfg)
            self._show_toast("已恢复出厂设置")

        self._enqueue_apply(work, on_ok)

    # ---------------- 配套工具 ----------------

    def _refresh_companion(self):
        """刷新配套工具分组：扩展状态 / 亮度 / GPU 重置记录。"""
        if ext_installed():
            state = "已启用" if ext_enabled() else "已安装（未启用）"
            self.ext_row.set_subtitle(state)
            self.ext_button.set_label("卸载")
            self.ext_button.remove_css_class("suggested-action")
            self.ext_button.add_css_class("destructive-action")
        else:
            self.ext_row.set_subtitle("未安装")
            self.ext_button.set_label("安装")
            self.ext_button.remove_css_class("destructive-action")
            self.ext_button.add_css_class("suggested-action")
        try:
            raw = brightness.get_brightness()
            pct = brightness.to_percent(raw) if raw is not None else None
        except OSError:
            pct = None
        if pct is None:
            self.brightness_row.set_subtitle("不可用（权限或设备缺失）")
            self.brightness_scale.set_sensitive(False)
            self._brightness_value = None
        else:
            value = int(round(pct))
            self.brightness_row.set_subtitle(f"当前 {value}%")
            self._syncing = True
            try:
                self.brightness_scale.set_value(value)
            finally:
                self._syncing = False
            self._brightness_value = value
            self.brightness_scale.set_sensitive(True)
        self.gpu_row.set_subtitle(gpu_reset_summary())

    def _on_ext_toggle(self, _btn):
        """安装/卸载 GNOME 快速设置扩展（按当前状态切换）。"""
        if ext_installed():
            ok, msg = uninstall_extension()
        else:
            ok, msg = install_extension()
        self._show_toast(msg, is_error=not ok)
        self._refresh_companion()

    def _on_brightness_changed(self, scale):
        if (self._syncing or self._initializing
                or self._brightness_value is None
                or int(round(scale.get_value())) == self._brightness_value):
            return
        value = int(round(scale.get_value()))

        def work():
            raw = brightness.from_percent(value)
            if raw is None:
                raise ValueError("无法换算亮度刻度（缺少 max_brightness）")
            if not brightness.set_brightness(raw):
                raise ValueError("亮度写入失败（权限或设备缺失）")

        def on_ok(_result):
            self._brightness_value = value
            self.brightness_row.set_subtitle(f"当前 {value}%")
            self._show_toast(f"亮度已设为 {value}%")

        self._enqueue_apply(work, on_ok)

    def _on_camera_probe(self, _btn):
        text = probe_cameras()
        self.camera_row.set_subtitle(text or "检测失败")
        if text:
            self._show_toast(f"相机: {text}")

    def _on_hw_probe(self, _btn):
        ok, msg = run_hw_probe()
        self._show_toast(msg, is_error=not ok)

    # ---------------- helpers ----------------

    def _show_toast(self, text, is_error=False):
        toast = Adw.Toast.new(text)
        if is_error:
            toast.set_timeout(5)
        self.toast_overlay.add_toast(toast)


class App(Adw.Application):
    def __init__(self, developer=False):
        super().__init__(application_id="io.github.z13touchpad.tool",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None
        self.developer = developer
        # 开发者信息开关：状态由环境变量/启动参数初始化，可在菜单中随时切换
        action = Gio.SimpleAction.new_stateful(
            "developer-mode", None, GLib.Variant.new_boolean(developer))
        action.connect("change-state", self._on_developer_mode)
        self.add_action(action)

    def _on_developer_mode(self, action, state):
        action.set_state(state)
        if self.win is not None:
            self.win._apply_developer_mode(bool(state.get_boolean()))

    def do_activate(self):
        if self.win is None:
            self.win = MainWindow(self, developer=self.developer)
        self.win.present()


def main():
    developer = ("--developer" in sys.argv
                 or "Z13_G2_TOOLS_DEVELOPER" in os.environ)
    app = App(developer)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
