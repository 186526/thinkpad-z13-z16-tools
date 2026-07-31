#!/usr/bin/env python3
"""可视化触控板组件（自绘 GTK DrawingArea）。

从 gui.py 拆出的独立模块，只依赖 GTK4 / libadwaita / pycairo，
不持有设备状态：数值与选中态由外部通过 set_values / set_selected /
set_main_force 驱动；点击条带内按键时回调 on_select(index)。
"""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (  # noqa: E402
    Adw, Gtk, Pango, PangoCairo,
)
try:  # noqa: E402
    import cairo  # pycairo，用于渐变等高级绘制
except ImportError:  # pragma: no cover
    cairo = None


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

        # 底板：自上而下的柔和渐变 + 细描边（替代生硬色块）
        self._rounded_rect(cr, 0, 0, w, h, radius)
        if cairo is not None:
            pat = cairo.LinearGradient(0, 0, 0, h)
            pat.add_color_stop_rgba(0, 1, 1, 1, 0.07)
            pat.add_color_stop_rgba(1, 1, 1, 0.015)
            cr.set_source(pat)
        else:
            cr.set_source_rgba(1, 1, 1, 0.04)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.set_line_width(1.0)
        cr.stroke()

        # 顶部条带内三键填充（选中 accent 淡色，悬停白色 5%，裁切在圆角内）
        for i in range(3):
            x0 = self._SPLITS[i] * w
            x1 = self._SPLITS[i + 1] * w
            key_radius = min(8.0, strip_h / 2, (x1 - x0) / 2)
            cr.save()
            self._rounded_rect(cr, 0, 0, w, h, radius)
            cr.clip()
            self._rounded_rect(cr, x0, 0, x1 - x0, strip_h, key_radius)
            if i == self._selected:
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.13)
                cr.fill()
            elif i == self._hovered:
                cr.set_source_rgba(1, 1, 1, 0.05)
                cr.fill()
            cr.restore()

        # 条带与主区分隔线 + 键间分隔（细实线，弱化，不再用粗虚线）
        cr.set_source_rgba(1, 1, 1, 0.09)
        cr.set_line_width(1.0)
        cr.move_to(2, strip_h)
        cr.line_to(w - 2, strip_h)
        cr.stroke()
        for i in (1, 2):
            lx = self._SPLITS[i] * w
            cr.move_to(lx, 1)
            cr.line_to(lx, strip_h - 1)
            cr.stroke()

        # 选中键描边（accent 亮边 + 内侧微光；贴边角跟随底板圆角）
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
            self._rounded_rect4(cr, x0 + 1, 1, kw, kh, r_tl, r_tr,
                                key_radius, key_radius)
            cr.set_source_rgba(accent[0], accent[1], accent[2], 0.55)
            cr.set_line_width(2.0)
            cr.stroke()
            # 内侧微光：更细腻的选中层次
            self._rounded_rect4(cr, x0 + 3, 3, kw - 4, kh - 4, r_tl - 2,
                                r_tr - 2, key_radius - 2, key_radius - 2)
            cr.set_source_rgba(accent[0], accent[1], accent[2], 0.18)
            cr.set_line_width(1.0)
            cr.stroke()
        elif self._hovered >= 0:
            x0 = self._SPLITS[self._hovered] * w
            x1 = self._SPLITS[self._hovered + 1] * w
            kw = x1 - x0 - 2
            kh = strip_h - 2
            key_radius = min(8.0, kh / 2, kw / 2)
            r_tl = r_tr = key_radius
            if self._hovered == 0:
                r_tl = radius
            elif self._hovered == 2:
                r_tr = radius
            self._rounded_rect4(cr, x0 + 1, 1, kw, kh, r_tl, r_tr,
                                key_radius, key_radius)
            cr.set_source_rgba(accent[0], accent[1], accent[2], 0.30)
            cr.set_line_width(1.5)
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
