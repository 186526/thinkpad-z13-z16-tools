#!/usr/bin/env python3
"""ThinkPad Z13/Z16 Gen 2 haptic touchpad settings GUI (GTK4 / libadwaita).

Three pages:
  * 设置     - adjust the haptic feedback intensity (0-100), the click /
               release thresholds of the main zone (with optional 65%
               release linkage and preset profiles), the per-zone click /
               release forces (left/right/middle), and optionally re-apply
               everything at login via a systemd user service (the device
               resets to defaults on every reboot).
  * HID 功能 - read-only view of every HID feature report the touchpad
               exposes (read via /dev/hidraw* ioctls).
  * 设备     - read-only device info (path, HID_ID, HID_NAME, HID_PHYS,
               driver, kernel module) parsed from /sys/class/hidraw sysfs.
"""

import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk, Adw  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import haptic  # noqa: E402
from haptic import TouchpadError  # noqa: E402

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

# 区域滑块配置：(区标题, 点击键, 释放键)
ZONE_GROUPS = (
    ("左区", "zone_left", "zone_left_release"),
    ("右区", "zone_right", "zone_right_release"),
    ("中区", "zone_middle", "zone_middle_release"),
)

# 预设档位显示名（键与 haptic.PROFILES 一致）
PROFILE_LABELS = {"light": "轻触", "standard": "标准", "heavy": "重按"}

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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Z13 触控板设置",
                         default_width=480, default_height=520)

        self.device = None
        self.current = None
        self._debounce_id = None
        self._apply_id = None
        self._syncing = False
        self._initializing = True

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text="重新检测并刷新")
        self.refresh_button.connect("clicked", self._on_refresh)
        header.pack_end(self.refresh_button)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_titled(self._build_settings_page(), "settings", "设置")
        self.stack.add_titled(self._build_features_page(), "features", "HID 功能")
        self.stack.add_titled(self._build_device_page(), "device", "设备")

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        header.set_title_widget(switcher)
        toolbar.add_top_bar(header)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)
        toolbar.set_content(self.toast_overlay)
        self.set_content(toolbar)

        self._load_state()
        self._initializing = False

    # ---------------- settings page ----------------

    def _build_settings_page(self):
        page = Adw.PreferencesPage()

        dev_group = Adw.PreferencesGroup(title="触控板")
        self.device_row = Adw.ActionRow(title="设备", subtitle="正在检测…")
        dev_group.add(self.device_row)
        page.add(dev_group)

        click_group = Adw.PreferencesGroup(
            title="点击力度",
            description="按压力阈值（固件寄存器 0x0038，默认 164g）。"
                        "调低 = 轻按触发点击，调高 = 重按触发。")
        self.click_label = Gtk.Label(label="164g", width_chars=6, xalign=1.0)
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

        self.release_label = Gtk.Label(label="108g", width_chars=6, xalign=1.0)
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
            title="释放阈值自动跟随 (65%)",
            subtitle="调整点击力度时自动设置释放阈值（建议保持开启）")
        self.link_switch.set_active(True)
        self.link_switch.connect("notify::active", self._on_link_toggled)

        self.profile_row = Adw.ComboRow(
            title="预设档位",
            subtitle="轻触 / 标准 / 重按：选中即写入主区点击与释放阈值")
        self.profile_model = Gtk.StringList.new(["轻触", "标准", "重按"])
        self.profile_row.set_model(self.profile_model)
        self.profile_row.connect("notify::selected", self._on_profile_selected)

        click_group.add(self.profile_row)
        click_group.add(click_box)
        click_group.add(release_box)
        click_group.add(self.link_switch)
        page.add(click_group)

        zone_group = Adw.PreferencesGroup(
            title="区域力度",
            description="左/右/中三个区域的点击与释放阈值（0x0091-0x0096，"
                        "默认 76g / 50g）。区域独立调节，不跟随主区 65% 联动。")
        self.zone_widgets = {}   # key -> (label, scale)
        for ztitle, click_key, release_key in ZONE_GROUPS:
            zone_group.add(Adw.ActionRow(title=ztitle))
            for key in (click_key, release_key):
                reg = haptic.REGISTERS_BY_KEY[key]
                label = Gtk.Label(label="—", width_chars=6, xalign=1.0)
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                 10, 500, 1)
                scale.set_hexpand(True)
                scale.set_draw_value(False)
                scale.connect("value-changed",
                              lambda s, k=key: self._on_zone_changed(s, k))
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                box.set_margin_top(8)
                box.set_margin_bottom(8)
                box.set_margin_start(16)
                box.set_margin_end(16)
                box.append(label)
                box.append(scale)
                zone_group.add(box)
                self.zone_widgets[key] = (label, scale)
        page.add(zone_group)

        haptic_group = Adw.PreferencesGroup(
            title="触感强度",
            description="触摸板点击的震动反馈力度（0-100，系统默认 50）")
        self.value_label = Gtk.Label(label=str(DEFAULT_INTENSITY), width_chars=3,
                                     xalign=1.0)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
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
            title="开机自动应用",
            description="重启后设备会恢复默认强度，通过 systemd 用户服务在登录时重新应用")
        self.autostart_switch = Adw.SwitchRow(title="登录后自动应用",
                                              subtitle="保存强度并在下次登录时应用")
        self.autostart_switch.connect("notify::active", self._on_autostart_toggled)
        auto_group.add(self.autostart_switch)
        page.add(auto_group)

        footer = Gtk.Label(
            label="Sensel 触控板 · 参考 Arch Wiki: Lenovo_ThinkPad_Z13/Z16_Gen_2")
        footer.set_halign(Gtk.Align.CENTER)
        footer.add_css_class("dim-label")
        footer_group = Adw.PreferencesGroup()
        footer_group.add(footer)
        page.add(footer_group)
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
        page.add(group)
        return page

    # ---------------- state ----------------

    def _load_state(self):
        self.device = haptic.find_device()
        if self.device is None:
            self.device_row.set_subtitle("未找到（未检测到 Sensel 触控板）")
            self.device_row.add_css_class("error")
            for row in self._device_rows.values():
                row.set_subtitle("未检测到设备")
            self._set_controls_enabled(False)
            return

        self.device_row.set_subtitle(self.device)
        info = haptic.device_info(self.device) or {}
        self._device_rows["设备路径"].set_subtitle(self.device)
        self._device_rows["HID_ID"].set_subtitle(info.get("HID_ID", "—") or "—")
        self._device_rows["HID_NAME"].set_subtitle(info.get("HID_NAME", "—") or "—")
        self._device_rows["HID_PHYS"].set_subtitle(info.get("HID_PHYS", "—") or "—")
        self._device_rows["驱动"].set_subtitle(info.get("DRIVER", "—") or "—")
        self._device_rows["内核模块"].set_subtitle(info.get("MODULE") or "未知")
        try:
            self.current = haptic.get_intensity(self.device)
            self._refresh_features()
            regs = haptic.read_all_registers(self.device)
        except TouchpadError as e:
            self.device_row.set_subtitle(f"{self.device}（{e}）")
            self._set_controls_enabled(False)
            return

        click = regs.get("click_force")
        if click is not None:
            self.click_scale.set_value(click * 2)
            self.click_label.set_text(f"{click * 2}g")
            # 选中与当前点击力度最接近的预设档（回填，不触发应用）
            names = list(haptic.PROFILES)
            best = min(names, key=lambda n: abs(
                haptic.PROFILES[n]["click_force"] - click * 2))
            self.profile_row.set_selected(names.index(best))
        release = regs.get("click_release")
        if release is not None:
            self.release_scale.set_value(release * 2)
            self.release_label.set_text(f"{release * 2}g")

        for key, (zone_label, zone_scale) in self.zone_widgets.items():
            raw = regs.get(key)
            if raw is not None:
                zone_scale.set_value(raw * 2)
                zone_label.set_text(f"{raw * 2}g")

        self.scale.set_value(self.current)
        self.value_label.set_text(str(self.current))
        self._set_controls_enabled(True)
        self.autostart_switch.set_active(autostart_enabled())

    def _set_controls_enabled(self, enabled):
        for w in (self.scale, self.reset_button, self.autostart_switch,
                  self.click_scale, self.release_scale, self.link_switch,
                  self.profile_row):
            w.set_sensitive(enabled)
        for _label, scale in self.zone_widgets.values():
            scale.set_sensitive(enabled)

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
        value = int(round(scale.get_value()))
        self.value_label.set_text(str(value))
        if self._initializing or self.device is None:
            return
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(400, self._apply)

    def _apply(self):
        self._debounce_id = None
        value = int(round(self.scale.get_value()))
        try:
            haptic.set_intensity(self.device, value)
        except TouchpadError as e:
            self._show_toast(str(e), is_error=True)
            self.scale.set_value(self.current)
            self.value_label.set_text(str(self.current))
            return GLib.SOURCE_REMOVE
        self.current = value
        save_config(haptic_intensity=value)
        self._show_toast(f"已应用触感强度 {value}")
        return GLib.SOURCE_REMOVE

    def _on_reset(self, _btn):
        self.scale.set_value(DEFAULT_INTENSITY)

    # ---------------- click force / release ----------------

    def _on_click_changed(self, scale):
        grams = int(round(scale.get_value()))
        self.click_label.set_text(f"{grams}g")
        if self._initializing or self.device is None or self._syncing:
            return
        if self.link_switch.get_active():
            self._sync_release_from_click()
        self._schedule_apply()

    def _on_release_changed(self, scale):
        grams = int(round(scale.get_value()))
        self.release_label.set_text(f"{grams}g")
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
        self.click_label.set_text(f"{raw_vals['click_force'] * 2}g")
        self.release_scale.set_value(raw_vals["click_release"] * 2)
        self.release_label.set_text(f"{raw_vals['click_release'] * 2}g")
        self._syncing = False
        save_config(**{key: raw_vals[key] * 2 for key in raw_vals})
        label = PROFILE_LABELS.get(name, name)
        self._show_toast(f"已应用预设「{label}」: 点击 {raw_vals['click_force'] * 2}g · "
                         f"释放 {raw_vals['click_release'] * 2}g")

    def _on_zone_changed(self, scale, key):
        grams = int(round(scale.get_value()))
        self.zone_widgets[key][0].set_text(f"{grams}g")
        if self._initializing or self.device is None or self._syncing:
            return
        self._schedule_apply()

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
        self.release_label.set_text(f"{rel_raw * 2}g")
        self._syncing = False

    def _schedule_apply(self):
        if self._apply_id is not None:
            GLib.source_remove(self._apply_id)
        self._apply_id = GLib.timeout_add(400, self._apply_all)

    def _apply_all(self):
        self._apply_id = None
        if self.device is None:
            return GLib.SOURCE_REMOVE
        click_g = int(round(self.click_scale.get_value()))
        click_raw = haptic.REGISTERS_BY_KEY["click_force"].from_human(click_g)
        if self.link_switch.get_active():
            rel_raw = max(1, round(click_raw * 0.65))
        else:
            rel_g = int(round(self.release_scale.get_value()))
            rel_raw = haptic.REGISTERS_BY_KEY["click_release"].from_human(rel_g)
        # 区域滑块各自换算成 raw（只写自己的寄存器，不碰主区）
        zone_raws = {}
        for key, (_label, scale) in self.zone_widgets.items():
            reg = haptic.REGISTERS_BY_KEY[key]
            grams = int(round(scale.get_value()))
            zone_raws[key] = reg.from_human(grams)
        try:
            haptic.write_register(self.device, 0x0038, click_raw)
            haptic.write_register(self.device, 0x0090, rel_raw)
            for key, raw in zone_raws.items():
                reg = haptic.REGISTERS_BY_KEY[key]
                haptic.write_register(self.device, reg.addr, raw)
        except haptic.RegisterError as e:
            self._show_toast(str(e), is_error=True)
            return GLib.SOURCE_REMOVE
        # 把滑块同步到设备真实值（raw*2），避免 165g -> raw 83 -> 166g 的显示偏差
        self._syncing = True
        self.click_scale.set_value(click_raw * 2)
        self.click_label.set_text(f"{click_raw * 2}g")
        if self.link_switch.get_active():
            self.release_scale.set_value(rel_raw * 2)
            self.release_label.set_text(f"{rel_raw * 2}g")
        for key, raw in zone_raws.items():
            label, scale = self.zone_widgets[key]
            scale.set_value(raw * 2)
            label.set_text(f"{raw * 2}g")
        self._syncing = False
        cfg = {"click_force": click_raw * 2, "click_release": rel_raw * 2}
        cfg.update({key: raw * 2 for key, raw in zone_raws.items()})
        save_config(**cfg)
        self._show_toast(f"点击力度 {click_raw * 2}g · 释放 {rel_raw * 2}g")
        return GLib.SOURCE_REMOVE

    # ---------------- autostart ----------------

    def _on_autostart_toggled(self, switch, _pspec):
        if self._initializing:
            return
        enabled = switch.get_active()
        err = set_autostart(enabled)
        if err:
            self._show_toast(err, is_error=True)
            with switch.handler_block_by_func(self._on_autostart_toggled):
                switch.set_active(not enabled)
        else:
            self._show_toast("已开启登录时自动应用" if enabled
                             else "已关闭登录时自动应用")

    # ---------------- helpers ----------------

    def _show_toast(self, text, is_error=False):
        toast = Adw.Toast.new(text)
        if is_error:
            toast.set_timeout(5)
        self.toast_overlay.add_toast(toast)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.z13touchpad.tool",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = MainWindow(self)
        self.win.present()


def main():
    app = App()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
