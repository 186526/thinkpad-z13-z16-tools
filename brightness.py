#!/usr/bin/env python3
"""ThinkPad Z13/Z16 Gen 2 OLED 亮度控制模块(纯 Python 标准库)。

三个能力:
1. 背光设备探测:在 /sys/class/backlight/* 里选"非 LED 面板"那个
   (type 属性为 firmware/platform/raw 优先;设备名不写死,多设备取第一个
   非 led)。
2. 读写:get_brightness() 读内核上报值(原始整数),
   set_brightness(v) 把同一原始值写回 brightness 文件(权限不足时给出
   明确中文错误并返回 False)。
3. Linux/Windows 亮度非线性换算:to_windows(lv) / to_linux(wv),
   锚点线性插值,仅为近似。

resume-watch 集成约定(并行 agent 按此调用,签名勿改):
    唤醒后等 1-2 秒,get_brightness() 读当前内核值,再 set_brightness(v)
    写回即可——内核 brightness 一直是用户上次设的值,显示器重新启用时却
    可能以默认亮度亮起,写回同值即强制面板刷新为内核上报值。

单元测试:python3 -m brightness(文件底部 assert 块)。
"""

import glob
import os
import sys

BACKLIGHT_DIR = "/sys/class/backlight"

# Linux <-> Windows 亮度换算锚点 (linux%, windows%)。
# 依据 Arch Wiki:30% Linux ≈ 75% Windows;其余锚点按 OLED 暗部拉低、
# 亮部趋平的经验形状自定。曲线为相邻锚点间线性插值,仅为近似。
BRIGHTNESS_ANCHORS = (
    (0, 0),
    (10, 20),
    (30, 75),   # wiki 锚点:30% Linux ≈ 75% Windows
    (70, 95),
    (100, 100),
)

# 背光 type 优先级(led 面板/键盘灯排除后按此顺序选)。
_TYPE_PRIORITY = ("firmware", "platform", "raw")

# 最近一次 find_backlight() 失败的原因(str|None),供调用方输出。
DETECT_ERROR = None


def find_backlight():
    """返回背光 sysfs 目录路径(如 /sys/class/backlight/amdgpu_bl1)。

    找不到返回 None,原因写入模块级 DETECT_ERROR。
    """
    global DETECT_ERROR
    DETECT_ERROR = None
    try:
        dirs = sorted(glob.glob(os.path.join(BACKLIGHT_DIR, "*")))
    except OSError as e:
        DETECT_ERROR = f"无法访问 {BACKLIGHT_DIR}: {e.strerror}"
        return None
    if not dirs:
        DETECT_ERROR = f"{BACKLIGHT_DIR} 下没有背光设备(可能无面板或驱动未加载)"
        return None

    non_led = []
    for d in dirs:
        try:
            with open(os.path.join(d, "type"), encoding="utf-8") as f:
                t = f.read().strip()
        except OSError:
            continue  # 读不到 type 的目录不算背光设备
        if t == "led":
            continue
        non_led.append((d, t))
    if not non_led:
        DETECT_ERROR = f"{BACKLIGHT_DIR} 下只有 led 类型设备,未找到面板背光"
        return None

    # firmware/platform/raw 优先,其余非 led 类型排后面
    def _key(item):
        t = item[1]
        return _TYPE_PRIORITY.index(t) if t in _TYPE_PRIORITY else len(_TYPE_PRIORITY)

    non_led.sort(key=_key)
    return non_led[0][0]


def get_max_brightness(dev=None):
    """读 max_brightness(原始整数),失败返回 None。"""
    dev = dev or find_backlight()
    if not dev:
        print(f"未找到背光设备: {DETECT_ERROR}", file=sys.stderr)
        return None
    try:
        with open(os.path.join(dev, "max_brightness"), encoding="utf-8") as f:
            return int(f.read().strip())
    except OSError as e:
        print(f"读取 max_brightness 失败: {e.strerror}", file=sys.stderr)
        return None
    except ValueError:
        print(f"max_brightness 内容无法解析: {os.path.join(dev, 'max_brightness')}",
              file=sys.stderr)
        return None


def get_brightness(dev=None):
    """读当前内核上报的亮度(原始整数),失败返回 None 并打印中文原因。"""
    dev = dev or find_backlight()
    if not dev:
        print(f"未找到背光设备: {DETECT_ERROR}", file=sys.stderr)
        return None
    try:
        with open(os.path.join(dev, "brightness"), encoding="utf-8") as f:
            return int(f.read().strip())
    except OSError as e:
        print(f"读取亮度失败: {e.strerror}", file=sys.stderr)
        return None
    except ValueError:
        print(f"brightness 内容无法解析: {os.path.join(dev, 'brightness')}",
              file=sys.stderr)
        return None


def set_brightness(value, dev=None):
    """把 brightness 写成 value(原始整数)。成功返回 True。

    失败返回 False,并向 stderr 打印明确中文错误(含权限不足场景)。
    """
    dev = dev or find_backlight()
    if not dev:
        print(f"未找到背光设备: {DETECT_ERROR}", file=sys.stderr)
        return False
    path = os.path.join(dev, "brightness")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(int(value)))
    except OSError as e:
        print(f"写入亮度失败: {e.strerror}(路径 {path})。"
              f"若是权限不足,授权方案见 docs/brightness-permissions.md。",
              file=sys.stderr)
        return False
    return True


def to_percent(raw, dev=None):
    """原始内核值 -> 百分比(0-100,一位小数)。max 不可读时返回 None。"""
    mx = get_max_brightness(dev)
    if mx is None or mx <= 0:
        return None
    return round(raw / mx * 100, 1)


def from_percent(pct, dev=None):
    """百分比(0-100)-> 原始内核值(四舍五入)。max 不可读时返回 None。"""
    mx = get_max_brightness(dev)
    if mx is None or mx <= 0:
        return None
    pct = max(0.0, min(100.0, float(pct)))
    return round(pct / 100 * mx)


def to_windows(lv):
    """Linux 亮度百分比 -> Windows 亮度百分比(锚点线性插值,近似)。"""
    lv = max(0, min(100, lv))
    for (l0, w0), (l1, w1) in zip(BRIGHTNESS_ANCHORS, BRIGHTNESS_ANCHORS[1:]):
        if l0 <= lv <= l1:
            if l1 == l0:
                return w0
            return w0 + (lv - l0) * (w1 - w0) / (l1 - l0)
    return BRIGHTNESS_ANCHORS[-1][1]


def to_linux(wv):
    """Windows 亮度百分比 -> Linux 亮度百分比(锚点线性插值逆运算,近似)。"""
    wv = max(0, min(100, wv))
    for (l0, w0), (l1, w1) in zip(BRIGHTNESS_ANCHORS, BRIGHTNESS_ANCHORS[1:]):
        if w0 <= wv <= w1:
            if w1 == w0:
                return l0
            return l0 + (wv - w0) * (l1 - l0) / (w1 - w0)
    return BRIGHTNESS_ANCHORS[-1][0]


if __name__ == "__main__":
    # ── 换算单元测试 ──────────────────────────────────────────────
    # 锚点
    assert to_windows(0) == 0 and to_windows(100) == 100
    assert to_windows(10) == 20 and to_linux(20) == 10
    assert to_windows(30) == 75 and to_linux(75) == 30   # wiki 锚点
    assert to_windows(70) == 95 and to_linux(95) == 70
    # 越界钳制
    assert to_windows(-1) == 0 and to_windows(101) == 100
    assert to_linux(-5) == 0 and to_linux(150) == 100
    # 单调 + 往返(段内线性,往返应精确)
    for v in range(0, 101):
        w = to_windows(v)
        assert 0 <= w <= 100, (v, w)
        assert abs(to_linux(w) - v) < 1e-6, (v, w, to_linux(w))
    # 段内抽查:20% Linux 落在 (10,20)-(30,75) 段
    assert abs(to_windows(20) - 47.5) < 1e-6, to_windows(20)

    # ── 背光探测(本机实况,不强制成功) ──────────────────────────
    dev = find_backlight()
    print(f"背光设备: {dev or DETECT_ERROR}")
    if dev:
        cur = get_brightness(dev)
        mx = get_max_brightness(dev)
        print(f"当前亮度: {cur} / {mx}({to_percent(cur, dev) if mx else '?'}%)")
    print("brightness 模块自测通过")
