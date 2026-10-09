"""IR/RGB 摄像头检测(z13-camera-tool / z13-hw-probe 共用)。

扫描 /sys/class/video4linux/video*,读取每个设备的 name,并向上查找上层
USB 设备的 idVendor/idProduct;IR 判定:名称含 IR(整词)或 USB ID 命中
IR_IDS。仅用标准库。
"""

import glob
import os
import re

# IR 相机 USB ID(Chicony Integrated IR Camera)。RGB 相机为 04f2:b78b。
IR_IDS = {("04f2", "b78c")}


def _usb_id(v4l_path):
    """从 /sys/class/video4linux/videoN 向上找到带 idVendor/idProduct 的目录。

    返回 (vendor, product) 小写四位数,找不到返回 None。
    """
    try:
        cur = os.path.realpath(v4l_path)
    except OSError:
        return None
    while True:
        vid = os.path.join(cur, "idVendor")
        if os.path.exists(vid):
            try:
                with open(vid, encoding="utf-8") as f:
                    v = f.read().strip().lower()
                with open(os.path.join(cur, "idProduct"), encoding="utf-8") as f:
                    p = f.read().strip().lower()
                return (v, p)
            except OSError:
                return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def scan():
    """列出全部 video4linux 设备:[{path, name, usb, ir}]。

    path —— /dev/videoN
    name —— 设备 name
    usb  —— "vvvv:pppp",找不到为 "未知"
    ir   —— 是否为 IR 相机(名称含 IR 整词或 USB ID 命中 IR_IDS)
    """
    out = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            with open(os.path.join(path, "name"), encoding="utf-8",
                      errors="replace") as f:
                name = f.read().strip()
        except OSError:
            name = "(读取 name 失败)"
        ids = _usb_id(path)
        ir = bool(re.search(r"\bir\b", name.lower())) or (ids in IR_IDS)
        out.append({
            "path": "/dev/" + os.path.basename(path),
            "name": name,
            "usb": f"{ids[0]}:{ids[1]}" if ids else "未知",
            "ir": ir,
        })
    return out
