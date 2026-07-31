#!/usr/bin/env python3
"""gui.py 驱动级 e2e 测试：每个控件（Option）的联动验证。

用内存假设备替换 haptic 的 IO 函数，避免触碰真实 /dev/hidraw。
在真实 X 显示下构建 MainWindow，直接驱动各 handler，断言
「控件状态 / 设备寄存器 / config 文件 / 触控板图状态」四者一致。

注意：不能直接迭代 GApplication 的主上下文来驱动异步完成回调——
手动 iteration() 会破坏 GApplication 的启动状态，进程退出时段错误
（真实 GUI 用 app.run() 无此问题）。因此测试把 _enqueue_apply
替换为同步执行 work()+on_ok 的替身，断言结果与真实路径等价。

运行（需 DISPLAY）：
    python3 tests/test_gui_e2e.py
"""

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("GDK_BACKEND", "x11")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from z13_tools.gui import app as gui  # noqa: E402
from z13_tools import haptic  # noqa: E402

ADDR_TO_KEY = {reg.addr: key for key, reg in haptic.REGISTERS_BY_KEY.items()}


class FakeDevice:
    """内存假触控板：基线 = 164/144g、三键 76/50g、强度 75。"""

    def __init__(self):
        self.regs = {
            "click_force": 82, "click_release": 72,
            "zone_left": 38, "zone_left_release": 25,
            "zone_middle": 38, "zone_middle_release": 25,
            "zone_right": 38, "zone_right_release": 25,
            "haptics_enabled": 1,
        }
        self.intensity = 75
        self.writes = []

    def write(self, addr, raw):
        key = ADDR_TO_KEY.get(addr)
        if key is None:
            return
        self.regs[key] = raw
        # 固件事实：report 11（强度）与寄存器 0x00AB 双向同步
        if key == "haptic_intensity":
            self.intensity = raw
        self.writes.append((addr, raw))


class GUITestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        gui.CONFIG_DIR = cls._tmp.name
        gui.CONFIG_FILE = os.path.join(cls._tmp.name, "config.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.fake = FakeDevice()
        haptic.find_device = lambda: "/dev/fake"
        haptic.get_intensity = lambda _d: self.fake.intensity
        haptic.set_intensity = lambda _d, v: setattr(self.fake, "intensity", v)
        haptic.read_all_registers = lambda _d: dict(self.fake.regs)
        haptic.write_register = lambda _d, addr, raw: self.fake.write(addr, raw)
        haptic.read_features = lambda _d: {}
        haptic.device_info = lambda _d: {
            "HID_ID": "00002C2F:00000028", "HID_NAME": "SNSL0028:00",
            "HID_PHYS": "i2c-1", "DRIVER": "hid-multitouch", "MODULE": "hid_multitouch",
        }
        self.auto_calls = []
        gui.set_autostart = lambda enabled: (self.auto_calls.append(enabled), None)[1]
        gui.autostart_enabled = lambda: False
        # 配套工具 mock：不碰真实 gsettings / 子进程 / sysfs
        self.brightness_writes = []
        gui.ext_installed = lambda: False
        gui.ext_enabled = lambda: False
        gui.install_extension = lambda: (True, "已安装")
        gui.uninstall_extension = lambda: (True, "已卸载")
        gui.probe_cameras = lambda: None
        gui.run_hw_probe = lambda: (True, "已写入 docs/hardware-findings.md")
        gui.gpu_reset_summary = lambda: "无重置记录"
        gui.brightness.get_brightness = lambda dev=None: 40
        gui.brightness.to_percent = lambda raw, dev=None: float(raw)
        gui.brightness.from_percent = lambda pct, dev=None: round(pct)
        gui.brightness.set_brightness = \
            lambda value, dev=None: (self.brightness_writes.append(int(value)),
                                     True)[1]
        self.app = gui.App(developer=False)
        self.app.do_activate()
        self.win = self.app.win
        self.assertIsNotNone(self.win)
        # 测试替身：异步写入队列改为同步执行（原因见模块 docstring）
        self.win._enqueue_apply = self._sync_enqueue

    def tearDown(self):
        self.win.close()
        if gui.CONFIG_FILE and os.path.exists(gui.CONFIG_FILE):
            os.remove(gui.CONFIG_FILE)

    # ---------------- helpers ----------------

    def _sync_enqueue(self, work, on_ok):
        """同步执行一次设备写入（work -> on_ok），替代真实的后台线程队列。"""
        try:
            work()
        except (haptic.TouchpadError, haptic.RegisterError, ValueError) as e:
            self.win._finish_apply_queue(False, str(e), on_ok)
            return
        self.win._finish_apply_queue(True, None, on_ok)

    def cfg(self):
        with open(gui.CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)

    def flush(self):
        """取消挂起的防抖/应用定时器，避免残留回调污染后续测试。"""
        for attr in ("_debounce_id", "_apply_id"):
            src = getattr(self.win, attr, None)
            if src is not None:
                GLib.source_remove(src)
                setattr(self.win, attr, None)

    def wait_applied(self, timeout=5.0):
        """同步替身下队列即时排空；保留参数以兼容真实异步实现。"""
        return not self.win._applying

    def apply_all(self):
        self.win._apply_all()
        self.flush()
        self.assertTrue(self.wait_applied(), "异步写入队列未排空")

    def assert_widgets_consistent(self):
        """四者一致性：zone_values / 图 / 滑块 / combo 选中。"""
        w = self.win
        ck, rk = w._zone_keys_for_selected()
        self.assertEqual(w.click_scale.get_value(), w.zone_values[ck])
        self.assertEqual(w.release_scale.get_value(), w.zone_values[rk])
        for i in range(3):
            self.assertEqual(w.touchpad_map._forces[i],
                             w.zone_values[gui.ZONE_MAP[i][1]])
            self.assertEqual(w.touchpad_map._releases[i],
                             w.zone_values[gui.ZONE_MAP[i][2]])
        self.assertEqual(w.touchpad_map._selected, w.selected_zone)
        self.assertEqual(w.zone_combo.get_selected(), w.selected_zone)

    # ---------------- 触感强度 ----------------

    def test_intensity_slider_applies_and_persists(self):
        w = self.win
        w.scale.set_value(80)          # 非 25 档值 -> 标签即时吸附 75
        self.assertEqual(w.value_label.get_text(), "75")
        w._apply()                     # 防抖后应用
        self.wait_applied()
        self.assertEqual(w.scale.get_value(), 75)
        self.assertEqual(self.fake.intensity, 75)
        self.assertEqual(self.cfg()["haptic_intensity"], 75)
        self.assertEqual(w.current, 75)

    def test_intensity_snaps_to_25_steps(self):
        """触感强度按 Windows 五档（0/25/50/75/100）吸附。"""
        w = self.win
        w.device = None                # 只验证吸附，不触发写入
        for raw, snapped in [(0, 0), (12, 0), (30, 25), (50, 50),
                             (62, 50), (70, 75), (88, 100), (100, 100)]:
            w.scale.set_value(raw)
            self.assertEqual(w.value_label.get_text(), str(snapped), f"raw={raw}")

    def test_reset_button(self):
        w = self.win
        w.scale.set_value(90)          # 吸附到 100
        w._apply()
        self.wait_applied()
        self.assertEqual(self.fake.intensity, 100)
        w._on_reset(None)              # 恢复默认强度 -> 50
        w._apply()
        self.wait_applied()
        self.assertEqual(self.fake.intensity, 50)
        self.assertEqual(self.cfg()["haptic_intensity"], 50)

    def test_reset_all_factory(self):
        """一键恢复出厂：所有寄存器写回固件默认，设备/UI/配置三者一致。"""
        w = self.win
        w.scale.set_value(90)
        w._apply()
        self.wait_applied()
        self.assertEqual(self.fake.intensity, 100)
        w._on_zone_pick(1)
        w.click_scale.set_value(120)
        self.apply_all()
        self.assertEqual(self.fake.regs["zone_middle"], 60)

        w._on_reset_all(None)          # 一键恢复出厂
        self.wait_applied()
        for reg in haptic.REGISTERS:
            self.assertEqual(self.fake.regs[reg.key], reg.default, reg.key)
        self.assertEqual(self.fake.intensity,
                         haptic.REGISTERS_BY_KEY["haptic_intensity"].default)
        cfg = self.cfg()
        self.assertEqual(cfg["haptic_intensity"], 50)
        self.assertEqual(cfg["click_force"], 164)
        self.assertEqual(cfg["click_release"], 108)
        self.assertEqual(cfg["zone_middle"], 76)
        self.assertEqual(cfg["zone_left_release"], 50)
        # UI 同步回出厂值（切到主点击区查看整板力度）
        w._set_selected_zone(3)
        self.assertEqual(int(round(w.click_scale.get_value())), 164)
        self.assertEqual(int(round(w.release_scale.get_value())), 108)
        self.assertEqual(w.scale.get_value(), 50)
        self.assertEqual(w.zone_values["zone_middle"], 76)

    def test_apply_bar_progress(self):
        """写入期间底部进度条可见，完成后隐藏（_show_applying 驱动）。"""
        w = self.win
        self.assertFalse(w.apply_bar.get_visible())
        w._show_applying(True)
        self.assertTrue(w.apply_bar.get_visible())
        w._show_applying(False)
        self.assertFalse(w.apply_bar.get_visible())

    def test_apply_failure_rolls_back(self):
        """写入失败：报错并重读设备回滚 UI 到真实值。"""
        w = self.win

        def fail_work():
            raise haptic.RegisterError("boom")

        w._enqueue_apply(fail_work, None)
        self.assertFalse(w._applying)
        self.assertFalse(w.apply_bar.get_visible())
        # on_ok 未执行（无新 toast 写入 config），UI 已按设备值回填
        w._set_selected_zone(3)
        self.assertEqual(int(round(w.click_scale.get_value())), 164)

    # ---------------- 按压力度（滑块/预设按选中区域读写） ----------------

    def test_click_link_release_65(self):
        w = self.win
        w._set_selected_zone(3)        # 编辑主点击区
        self.assertTrue(w.link_switch.get_active())
        w.click_scale.set_value(200)   # 联动：释放自动 65%
        self.assertEqual(int(round(w.release_scale.get_value())), 130)
        self.apply_all()
        self.assertEqual(self.fake.regs["click_force"], 100)   # 200g / 2
        self.assertEqual(self.fake.regs["click_release"], 65)  # 130g / 2
        cfg = self.cfg()
        self.assertEqual(cfg["click_force"], 200)
        self.assertEqual(cfg["click_release"], 130)
        # 图上主区文字同步
        self.assertEqual(w.touchpad_map._main_force_g, 200)
        # 滑块被回填为 raw*2（无 165->166 类偏差）
        self.assertEqual(int(round(w.click_scale.get_value())), 200)

    def test_click_without_link(self):
        w = self.win
        w._set_selected_zone(3)        # 编辑主点击区
        w.link_switch.set_active(False)
        w.click_scale.set_value(240)
        self.apply_all()
        self.assertEqual(self.fake.regs["click_force"], 120)
        self.assertEqual(self.fake.regs["click_release"], 72)  # 释放保持

    def test_release_slider_without_link(self):
        w = self.win
        w._set_selected_zone(3)        # 编辑主点击区
        w.link_switch.set_active(False)
        w.release_scale.set_value(100)
        self.apply_all()
        self.assertEqual(self.fake.regs["click_release"], 50)
        self.assertEqual(self.cfg()["click_release"], 100)

    def test_profile_preset_main_area(self):
        w = self.win
        w._set_selected_zone(3)        # 主点击区预设（整板力度）
        # 轻触档：点击/释放写设备 + 滑块/图上主区/配置全部同步
        names = list(haptic.PROFILES)
        w.profile_row.set_selected(names.index("light"))
        for key, grams in haptic.PROFILES["light"].items():
            reg = haptic.REGISTERS_BY_KEY[key]
            self.assertEqual(self.fake.regs[key], reg.from_human(grams))
        self.assertEqual(
            int(round(w.click_scale.get_value())),
            haptic.PROFILES["light"]["click_force"])
        self.assertEqual(
            int(round(w.release_scale.get_value())),
            haptic.PROFILES["light"]["click_release"])
        self.assertEqual(w.touchpad_map._main_force_g,
                         haptic.PROFILES["light"]["click_force"])
        cfg = self.cfg()
        self.assertEqual(cfg["click_force"],
                         haptic.PROFILES["light"]["click_force"])
        self.assertEqual(cfg["click_release"],
                         haptic.PROFILES["light"]["click_release"])

    # ---------------- 顶部按键（图上分区 / 区域联动） ----------------

    def test_map_click_selects_zone(self):
        w = self.win
        w._on_zone_pick(1)             # 模拟点击图上中键
        self.assertEqual(w.selected_zone, 1)
        self.assertEqual(w.zone_combo.get_selected(), 1)
        self.assertEqual(w.touchpad_map._selected, 1)
        # 滑块回填中键值
        self.assertEqual(w.click_scale.get_value(),
                         w.zone_values["zone_middle"])
        self.assert_widgets_consistent()

    def test_combo_selects_zone(self):
        w = self.win
        w.zone_combo.set_selected(2)   # 下拉选右键
        self.assertEqual(w.selected_zone, 2)
        self.assertEqual(w.touchpad_map._selected, 2)
        self.assert_widgets_consistent()

    def test_main_area_selectable_and_applies(self):
        """主点击区是图内第 4 个区域：图上点击/下拉选中后，
        同一组滑块编辑 click_force/click_release 并写设备。"""
        w = self.win
        w._on_zone_pick(3)             # 点击图上主点击区
        self.assertEqual(w.selected_zone, 3)
        self.assertEqual(w.zone_combo.get_selected(), 3)
        self.assertEqual(w.touchpad_map._selected, 3)
        self.assertEqual(w.click_scale.get_value(),
                         w.zone_values["click_force"])
        self.assert_widgets_consistent()
        # 改主区力度并应用；再切到左键，滑块回填左键值（联动不串值）
        w.link_switch.set_active(False)
        w.click_scale.set_value(200)
        self.apply_all()
        self.assertEqual(self.fake.regs["click_force"], 100)
        self.assertEqual(self.cfg()["click_force"], 200)
        w._on_zone_pick(0)
        self.assertEqual(w.click_scale.get_value(),
                         w.zone_values["zone_left"])
        self.assertEqual(w.touchpad_map._selected, 0)
        self.assert_widgets_consistent()

    def test_zone_click_slider_applies(self):
        w = self.win
        w._on_zone_pick(1)             # 选中中键
        w.click_scale.set_value(120)
        self.assertEqual(w.zone_values["zone_middle"], 120)
        self.assertEqual(w.click_label.get_text(), "点击 120g")
        self.assertEqual(w.touchpad_map._forces[1], 120)  # 图上数值同步
        self.apply_all()
        self.assertEqual(self.fake.regs["zone_middle"], 60)
        self.assertEqual(self.cfg()["zone_middle"], 120)
        # 其它键不受影响
        self.assertEqual(self.fake.regs["zone_left"], 38)
        self.assertEqual(self.fake.regs["zone_right"], 38)
        self.assert_widgets_consistent()

    def test_zone_release_slider_applies(self):
        w = self.win
        w.link_switch.set_active(False)  # 关闭联动才能独立改释放
        w._on_zone_pick(2)             # 选中右键
        w.release_scale.set_value(80)
        self.assertEqual(w.zone_values["zone_right_release"], 80)
        self.assertEqual(w.release_label.get_text(), "释放 80g")
        self.assertEqual(w.touchpad_map._releases[2], 80)
        self.apply_all()
        self.assertEqual(self.fake.regs["zone_right_release"], 40)
        self.assertEqual(self.cfg()["zone_right_release"], 80)
        self.assert_widgets_consistent()

    def test_zone_independent_values(self):
        """三个键各自独立：改中键不影响左/右。"""
        w = self.win
        w._on_zone_pick(0)
        w.click_scale.set_value(90)
        self.apply_all()
        w._on_zone_pick(1)
        w.click_scale.set_value(150)
        self.apply_all()
        self.assertEqual(self.fake.regs["zone_left"], 45)
        self.assertEqual(self.fake.regs["zone_middle"], 75)
        self.assertEqual(self.fake.regs["zone_right"], 38)

    def test_profile_preset_applies_to_keys(self):
        """预设档位：选中三键区域时，一键把左/中/右三键设为同一力度。"""
        w = self.win
        w._on_zone_pick(1)             # 选中中键
        names = list(gui.ZONE_PRESETS)
        w.profile_row.set_selected(names.index("heavy"))
        for _ztitle, click_key, release_key in gui.ZONE_MAP:
            self.assertEqual(
                self.fake.regs[click_key],
                haptic.REGISTERS_BY_KEY[click_key].from_human(
                    gui.ZONE_PRESETS["heavy"]["click"]))
            self.assertEqual(
                self.fake.regs[release_key],
                haptic.REGISTERS_BY_KEY[release_key].from_human(
                    gui.ZONE_PRESETS["heavy"]["release"]))
            self.assertEqual(w.zone_values[click_key], 110)
            self.assertEqual(w.zone_values[release_key], 72)
        cfg = self.cfg()
        self.assertEqual(cfg["zone_left"], 110)
        self.assertEqual(cfg["zone_right_release"], 72)
        self.assert_widgets_consistent()

    def test_out_of_range_zone_index_ignored(self):
        w = self.win
        w._set_selected_zone(99)
        self.assertEqual(w.selected_zone, 0)
        w._set_selected_zone(-1)
        self.assertEqual(w.selected_zone, 0)

    # ---------------- 配套工具 ----------------

    def test_ext_status_display(self):
        w = self.win
        gui.ext_installed = lambda: True
        gui.ext_enabled = lambda: True
        w._refresh_companion()
        self.assertEqual(w.ext_row.get_subtitle(), "已启用")
        self.assertEqual(w.ext_button.get_label(), "卸载")
        gui.ext_enabled = lambda: False
        w._refresh_companion()
        self.assertEqual(w.ext_row.get_subtitle(), "已安装（未启用）")
        gui.ext_installed = lambda: False
        w._refresh_companion()
        self.assertEqual(w.ext_row.get_subtitle(), "未安装")
        self.assertEqual(w.ext_button.get_label(), "安装")

    def test_ext_toggle_installs_then_uninstalls(self):
        w = self.win
        # 未安装 -> 点击 = 安装
        installed = []
        gui.install_extension = lambda: (installed.append(1), (True, "已安装"))[1]
        w._on_ext_toggle(None)
        self.assertEqual(installed, [1])
        # 已安装 -> 点击 = 卸载
        gui.ext_installed = lambda: True
        uninstalled = []
        gui.uninstall_extension = lambda: (uninstalled.append(1), (True, "已卸载"))[1]
        w._on_ext_toggle(None)
        self.assertEqual(uninstalled, [1])
        self.assertEqual(w.ext_button.get_label(), "卸载")

    def test_brightness_slider_applies(self):
        w = self.win
        self.assertEqual(w.brightness_row.get_subtitle(), "当前 40%")
        self.assertEqual(w.brightness_scale.get_value(), 40)
        self.assertTrue(w.brightness_scale.get_sensitive())
        w.brightness_scale.set_value(60)
        self.assertEqual(self.brightness_writes, [60])
        self.assertEqual(w.brightness_row.get_subtitle(), "当前 60%")

    def test_brightness_unavailable_disables_slider(self):
        w = self.win
        gui.brightness.get_brightness = lambda dev=None: None
        w._refresh_companion()
        self.assertEqual(w.brightness_row.get_subtitle(),
                         "不可用（权限或设备缺失）")
        self.assertFalse(w.brightness_scale.get_sensitive())

    def test_camera_probe(self):
        w = self.win
        gui.probe_cameras = lambda: "Integrated Camera (RGB) · 集成 IR 相机 (IR)"
        w._on_camera_probe(None)
        self.assertEqual(w.camera_row.get_subtitle(),
                         "Integrated Camera (RGB) · 集成 IR 相机 (IR)")
        gui.probe_cameras = lambda: None
        w._on_camera_probe(None)
        self.assertEqual(w.camera_row.get_subtitle(), "检测失败")

    def test_gpu_reset_summary(self):
        w = self.win
        gui.gpu_reset_summary = lambda: "最近: 2026-07-31 12:00 amdgpu reset"
        w._refresh_companion()
        self.assertEqual(w.gpu_row.get_subtitle(),
                         "最近: 2026-07-31 12:00 amdgpu reset")

    def test_hw_probe(self):
        w = self.win
        ran = []
        gui.run_hw_probe = lambda: (ran.append(1), (True, "已写入硬件探测结果"))[1]
        w._on_hw_probe(None)
        self.assertEqual(ran, [1])

    # ---------------- 自动恢复开关 ----------------

    def test_autostart_toggle(self):
        w = self.win
        w.autostart_switch.set_active(True)
        self.assertEqual(self.auto_calls, [True])
        w.autostart_switch.set_active(False)
        self.assertEqual(self.auto_calls, [True, False])

    # ---------------- 刷新 ----------------

    def test_refresh_reloads_state(self):
        w = self.win
        w._set_selected_zone(3)        # 编辑主点击区，刷新后仍按主区回填
        w.click_scale.set_value(300)
        self.apply_all()
        # 外部改动设备（如固件默认）
        self.fake.regs["click_force"] = 50
        self.fake.regs["click_release"] = 40
        self.fake.intensity = 20
        w._on_refresh(None)
        self.assertEqual(int(round(w.click_scale.get_value())), 100)  # 50*2
        self.assertEqual(int(round(w.release_scale.get_value())), 80)
        self.assertEqual(w.scale.get_value(), 25)  # 20 -> 吸附到 25
        self.assertEqual(w.touchpad_map._main_force_g, 100)
        self.assert_widgets_consistent()

    # ---------------- 开发者模式 ----------------

    def test_developer_mode_pages(self):
        w = self.win
        self.assertFalse(w._dev_pages_added)
        # 普通模式 stack 只有设置页
        self.assertEqual(w.stack.get_pages().get_n_items(), 1)
        w._apply_developer_mode(True)
        self.assertTrue(w._dev_pages_added)
        self.assertEqual(w.stack.get_pages().get_n_items(), 3)
        self.assertTrue(hasattr(w, "_feature_rows"))
        self.assertTrue(hasattr(w, "_device_rows"))
        w._apply_developer_mode(False)
        self.assertFalse(w._dev_pages_added)
        self.assertEqual(w.stack.get_pages().get_n_items(), 1)

    # ---------------- 设备缺失 ----------------

    def test_no_device_disables_controls(self):
        haptic.find_device = lambda: None
        w = self.win
        w._load_state()
        self.assertEqual(w.device_row.get_subtitle(), "未检测到支持的触控板")
        self.assertFalse(w.scale.get_sensitive())
        self.assertFalse(w.touchpad_map.get_sensitive())


def main():
    # 任何 GTK 启动期告警只打 stderr，不中断测试
    ok = unittest.main(argv=[sys.argv[0]], verbosity=2, exit=False)
    sys.exit(0 if ok.result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
