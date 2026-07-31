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
try:  # noqa: E402
    import cairo  # pycairo，用于渐变等高级绘制
except ImportError:  # pragma: no cover
    cairo = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import haptic  # noqa: E402
from haptic import TouchpadError  # noqa: E402
import brightness  # noqa: E402

# 非 GTK 支撑逻辑（常量 / 配置 / 自启动 / 配套工具）与自绘触控板组件
# 拆分为独立模块；这里 re-export 全部公共名字，保持 tests/test_gui_e2e.py
# 对 gui 模块级名字的 monkeypatch 兼容。
import gui_utils  # noqa: E402
from gui_utils import *  # noqa: E402,F403
from touchpad_map import TouchpadMap  # noqa: E402


def load_config():
    """读配置文件；把 gui 命名空间的 CONFIG_FILE 传给实现。

    测试 monkeypatch gui.CONFIG_FILE 到临时路径后，这里必须读到新值，
    因此不能直接用 gui_utils.load_config（它读 gui_utils.CONFIG_FILE）。
    """
    return gui_utils.load_config(CONFIG_FILE)


def save_config(**values):
    """写配置文件；同上，把 gui.CONFIG_FILE 显式传给实现。"""
    gui_utils.save_config(CONFIG_FILE, **values)


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

        title_label = Adw.WindowTitle(title="Z13/Z16 Gen 2 工具箱",
                                      subtitle="ThinkPad 触控板与硬件设置")
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

        force_group = Adw.PreferencesGroup(
            title="按压力度",
            description="整板主点击区与上沿三个按键的按压力度。"
                        "调低后轻按即可点击；调高后需要更用力。")

        # 触控板图：左/中/右三键 + 主点击区四个区域均可点击选中
        touchpad_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        touchpad_wrap.set_halign(Gtk.Align.CENTER)
        touchpad_wrap.set_margin_top(12)
        touchpad_wrap.set_margin_bottom(8)
        self.touchpad_map = TouchpadMap(on_select=self._on_zone_pick)
        touchpad_wrap.append(self.touchpad_map)
        force_group.add(touchpad_wrap)

        # 全部区域的力度模型（克数）：click_force/click_release 主点击区
        # + 六个三键键；滑块/预设都是「当前选中区域」的编辑器
        self.zone_values = {}   # key -> 克数
        for key in ("click_force", "click_release"):
            self.zone_values[key] = 0
        for _ztitle, click_key, release_key in ZONE_MAP:
            self.zone_values[click_key] = 0
            self.zone_values[release_key] = 0
        self.selected_zone = 0

        self.zone_combo = Adw.ComboRow(
            title="要调整的区域",
            subtitle="点击上方图形或在此选择要调节的区域")
        self.zone_combo_model = Gtk.StringList.new(
            [ztitle for ztitle, _ck, _rk in ZONE_MAP] + ["主点击区"])
        self.zone_combo.set_model(self.zone_combo_model)
        self.zone_combo.set_selected(0)
        self.zone_combo.connect("notify::selected", self._on_zone_combo_changed)
        force_group.add(self.zone_combo)

        # 预设档位：按当前选中区域应用——主点击区用整板预设，
        # 三个按键统一用按键预设（左/中/右同一力度）
        self.profile_row = Adw.ComboRow(
            title="预设档位",
            subtitle="按当前选中的区域应用默认力度")
        self.profile_model = Gtk.StringList.new(["轻触", "标准", "重按"])
        self.profile_row.set_model(self.profile_model)
        self.profile_row.connect("notify::selected", self._on_profile_selected)
        force_group.add(self.profile_row)

        self.click_label = Gtk.Label(label="点击 —", width_chars=10, xalign=1.0)
        self.click_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                    10, 500, 1)
        self.click_scale.set_hexpand(True)
        self.click_scale.set_draw_value(False)
        self.click_scale.connect("value-changed", self._on_click_changed)
        click_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        click_box.add_css_class("card")
        click_box.set_margin_top(4)
        click_box.set_margin_bottom(4)
        click_box.append(self.click_label)
        click_box.append(self.click_scale)
        force_group.add(click_box)

        self.release_label = Gtk.Label(label="释放 —", width_chars=10, xalign=1.0)
        self.release_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                      10, 500, 1)
        self.release_scale.set_hexpand(True)
        self.release_scale.set_draw_value(False)
        self.release_scale.connect("value-changed", self._on_release_changed)
        release_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        release_box.add_css_class("card")
        release_box.set_margin_top(4)
        release_box.set_margin_bottom(4)
        release_box.append(self.release_label)
        release_box.append(self.release_scale)
        force_group.add(release_box)

        self.link_switch = Adw.SwitchRow(
            title="释放力度自动跟随",
            subtitle="保持点击和松开时的力度比例自然（推荐）")
        self.link_switch.set_active(True)
        self.link_switch.connect("notify::active", self._on_link_toggled)
        force_group.add(self.link_switch)
        page.add(force_group)

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
        slider_box.add_css_class("card")
        slider_box.set_margin_top(4)
        slider_box.set_margin_bottom(4)
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

        # ---------------- 配套工具：按操作类型分三组 ----------------
        # 开关与调节：日常控制类，放最前
        control_group = Adw.PreferencesGroup(
            title="开关与调节",
            description="GNOME 快速设置开关与 OLED 亮度。")
        # GNOME 扩展：未安装 -> 安装；已安装 -> 卸载
        self.ext_row = Adw.ActionRow(title="GNOME 快速设置开关",
                                     subtitle="未检测")
        self.ext_button = Gtk.Button(label="安装")
        self.ext_button.add_css_class("suggested-action")
        self.ext_button.connect("clicked", self._on_ext_toggle)
        self.ext_row.add_suffix(self.ext_button)
        control_group.add(self.ext_row)

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
        brightness_box.add_css_class("card")
        brightness_box.set_margin_top(4)
        brightness_box.set_margin_bottom(4)
        brightness_box.append(self.brightness_scale)
        control_group.add(self.brightness_row)
        control_group.add(brightness_box)
        page.add(control_group)

        # 检测与维护：按需运行的一次性工具
        tools_group = Adw.PreferencesGroup(
            title="检测与维护",
            description="相机检测与硬件探测。")
        # 相机检测
        self.camera_row = Adw.ActionRow(title="相机检测", subtitle="未检测")
        self.camera_button = Gtk.Button(label="检测")
        self.camera_button.connect("clicked", self._on_camera_probe)
        self.camera_row.add_suffix(self.camera_button)
        tools_group.add(self.camera_row)

        # 硬件探测
        self.probe_row = Adw.ActionRow(title="硬件探测",
                                       subtitle="指纹 / TPM / 相机")
        self.probe_button = Gtk.Button(label="运行")
        self.probe_button.connect("clicked", self._on_hw_probe)
        self.probe_row.add_suffix(self.probe_button)
        tools_group.add(self.probe_row)
        page.add(tools_group)

        # 状态与记录：只读诊断展示，不伪装成可配置项
        status_group = Adw.PreferencesGroup(
            title="状态与记录",
            description="GPU 重置监控（只读，常驻监听由 systemd 服务运行）。")
        self.gpu_row = Adw.ActionRow(title="GPU 重置监控",
                                     subtitle="无重置记录")
        status_group.add(self.gpu_row)
        page.add(status_group)

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

        for key in self.zone_values:
            raw = regs.get(key)
            if raw is not None:
                self.zone_values[key] = raw * 2
        # 统一入口：同时刷新 combo/高亮/滑块/预设行/图上数值，修复选中态分歧
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
                  self.profile_row, self.zone_combo, self.touchpad_map,
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
        ck, _rk = self._zone_keys_for_selected()
        self.zone_values[ck] = grams
        if self.link_switch.get_active():
            self._sync_release_from_click()
        self._push_to_map()
        self._schedule_apply()

    def _on_release_changed(self, scale):
        grams = int(round(scale.get_value()))
        self.release_label.set_text(f"释放 {grams}g")
        if self._initializing or self.device is None or self._syncing:
            return
        if not self.link_switch.get_active():
            _ck, rk = self._zone_keys_for_selected()
            self.zone_values[rk] = grams
            self._push_to_map()
            self._schedule_apply()

    def _on_profile_selected(self, row, _pspec):
        """预设档位：按当前选中区域应用——主点击区用整板预设，
        顶部三键统一用按键预设（左/中/右同一力度）。"""
        if self._initializing or self._syncing or self.device is None:
            return
        idx = row.get_selected()
        names = list(haptic.PROFILES)
        if not 0 <= idx < len(names):
            return
        name = names[idx]
        if self.selected_zone == 3:
            targets = list(haptic.PROFILES[name].items())
            area = "主点击区"
        else:
            preset = ZONE_PRESETS[name]      # {"click": g, "release": g}
            targets = []
            for _ztitle, click_key, release_key in ZONE_MAP:
                targets.append((click_key, preset["click"]))
                targets.append((release_key, preset["release"]))
            area = "顶部按键"
        raw_vals = {key: haptic.REGISTERS_BY_KEY[key].from_human(grams)
                    for key, grams in targets}

        def work():
            for key, raw in raw_vals.items():
                reg = haptic.REGISTERS_BY_KEY[key]
                haptic.write_register(self.device, reg.addr, raw)

        def on_ok(_result):
            # 同步模型/滑块/图/配置（raw*2 显示，与 _apply_all 一致）
            self._syncing = True
            for key, raw in raw_vals.items():
                self.zone_values[key] = raw * 2
            self._syncing = False
            self._sync_force_sliders()
            self._push_to_map()
            save_config(**{key: raw * 2 for key, raw in raw_vals.items()})
            label = PROFILE_LABELS.get(name, name)
            self._show_toast(f"已应用预设「{label}」至{area}")

        self._enqueue_apply(work, on_ok)

    def _zone_keys_for_selected(self):
        """当前选中区域对应的 (点击键, 释放键)；主点击区指向整板寄存器。"""
        if self.selected_zone == 3:
            return "click_force", "click_release"
        _ztitle, click_key, release_key = ZONE_MAP[self.selected_zone]
        return click_key, release_key

    def _push_to_map(self):
        """把三键与主点击区数值推给触控板图重绘。"""
        self.touchpad_map.set_values(
            [self.zone_values[ZONE_MAP[i][1]] for i in range(3)],
            [self.zone_values[ZONE_MAP[i][2]] for i in range(3)])
        self.touchpad_map.set_main_force(self.zone_values.get("click_force", 0))

    def _sync_force_sliders(self):
        """按当前选中区域回填两条滑块（_syncing 防抖，不触发应用）。"""
        click_key, release_key = self._zone_keys_for_selected()
        self._syncing = True
        self.click_scale.set_value(self.zone_values[click_key])
        self.click_label.set_text(f"点击 {self.zone_values[click_key]}g")
        self.release_scale.set_value(self.zone_values[release_key])
        self.release_label.set_text(f"释放 {self.zone_values[release_key]}g")
        self._syncing = False

    def _backfill_preset_row(self):
        """按当前选中区域的点击力度回填预设档位选中值（不触发应用）。"""
        if self.selected_zone == 3:
            table, key = haptic.PROFILES, "click_force"
        else:
            table, key = ZONE_PRESETS, "click"
        ck, _rk = self._zone_keys_for_selected()
        grams = self.zone_values.get(ck, 0)
        names = list(table)
        best = min(names, key=lambda n: abs(table[n][key] - grams))
        self._syncing = True
        self.profile_row.set_selected(names.index(best))
        self._syncing = False

    def _set_selected_zone(self, index):
        """切换选中区域：统一同步 combo、图上高亮、滑块与预设行。

        MainWindow.selected_zone 是唯一选中态数据源，所有路径
        （图上点击 / 下拉选择 / 设备刷新）都从这里同步。
        """
        if not 0 <= index < 4:
            return
        self.selected_zone = index
        self.touchpad_map.set_selected(index)
        hid = self.zone_combo.handler_block_by_func(self._on_zone_combo_changed)
        try:
            self.zone_combo.set_selected(index)
        finally:
            self.zone_combo.handler_unblock_by_func(self._on_zone_combo_changed)
        self._sync_force_sliders()
        self._backfill_preset_row()
        self._push_to_map()

    def _on_zone_pick(self, index):
        if self._initializing or self.device is None:
            return
        self._set_selected_zone(index)

    def _on_zone_combo_changed(self, row, _pspec):
        if self._initializing or self.device is None:
            return
        self._set_selected_zone(row.get_selected())

    def _on_link_toggled(self, switch, _pspec):
        if self._initializing:
            return
        if switch.get_active():
            self._sync_release_from_click()

    def _sync_release_from_click(self):
        """Release raw = 65% of the click raw (grams/2)，作用于当前选中区域。"""
        click_key, release_key = self._zone_keys_for_selected()
        click_g = int(round(self.click_scale.get_value()))
        click_raw = haptic.REGISTERS_BY_KEY[click_key].from_human(click_g)
        rel_raw = max(1, round(click_raw * 0.65))
        self._syncing = True
        self.release_scale.set_value(rel_raw * 2)
        self.release_label.set_text(f"释放 {rel_raw * 2}g")
        self.zone_values[release_key] = rel_raw * 2
        self._syncing = False

    def _schedule_apply(self):
        if self._apply_id is not None:
            GLib.source_remove(self._apply_id)
        self._apply_id = GLib.timeout_add(400, self._apply_all)

    def _apply_all(self):
        self._apply_id = None
        if self.device is None:
            return GLib.SOURCE_REMOVE
        # 当前滑块值先写回对应区域模型（含 link 联动释放），再全量写设备
        ck, rk = self._zone_keys_for_selected()
        self.zone_values[ck] = int(round(self.click_scale.get_value()))
        if self.link_switch.get_active():
            click_raw = haptic.REGISTERS_BY_KEY[ck].from_human(self.zone_values[ck])
            rel_raw = max(1, round(click_raw * 0.65))
            self.zone_values[rk] = rel_raw * 2
        else:
            self.zone_values[rk] = int(round(self.release_scale.get_value()))
        raw_values = {key: haptic.REGISTERS_BY_KEY[key].from_human(grams)
                      for key, grams in self.zone_values.items()}

        def work():
            # 后台线程执行设备 ioctl（含 ACK 等待），避免 UI 卡顿
            for key, raw in raw_values.items():
                reg = haptic.REGISTERS_BY_KEY[key]
                haptic.write_register(self.device, reg.addr, raw)

        def on_ok(_result):
            # 把滑块同步到设备真实值（raw*2），避免 165g -> raw 83 -> 166g 显示偏差
            self._syncing = True
            for key, raw in raw_values.items():
                self.zone_values[key] = raw * 2
            self._syncing = False
            self._sync_force_sliders()
            self._push_to_map()
            save_config(**self.zone_values)
            area = ("主点击区" if self.selected_zone == 3
                    else ZONE_MAP[self.selected_zone][0])
            self._show_toast(f"{area}: 点击 {self.zone_values[ck]}g · "
                             f"释放 {self.zone_values[rk]}g")

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
            # 已有结果后按钮文案变为「重新检测」，语义更准确
            self.camera_button.set_label("重新检测")
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
        super().__init__(application_id="io.github.thinkpad-z13-z16-tools",
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
