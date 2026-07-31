#!/usr/bin/env python3
"""危险实验:逐字节探索报告 7(Win8 PTP 256 字节参数区)。

⚠⚠ 这是危险实验:需要图形会话里人工行为测试(点击/拖动/双指/轻扫)。
写入未知字段可能改变触控板行为直到重启。写前自动快照,退出(含 Ctrl+C /
异常)必还原;重启也能把设备重置为默认。

用法:
    blob-sweep.py --dump                     列出参数区非零字节
    blob-sweep.py --sweep <offset> [value]   单字节实验:把 offset 字节改为
                                             value(默认 0xFF),提示人工测试
                                             后自动还原;每轮只改一个字节
    blob-sweep.py --restore                  从快照还原参数区
    blob-sweep.py --device PATH              指定 hidraw 设备(默认自动探测)

快照文件 /tmp/z13_blob_sweep_snapshot.bin 独立于 feature-probe.py 的
/tmp/z13_bank_snapshot.bin,两者互不覆盖。实验记录写入
~/.config/z13-g2-tools/blob-sweep.log(运行时状态,不写进仓库)。
"""

import argparse
import fcntl
import os
import sys
import time

# 仓库根 = 向上找含 z13_tools 包的祖先目录（源码树与 AppImage 通用）。
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(_REPO_ROOT, "z13_tools")):
    _parent = os.path.dirname(_REPO_ROOT)
    if _parent == _REPO_ROOT:
        break
    _REPO_ROOT = _parent
sys.path.insert(0, _REPO_ROOT)
from z13_tools import haptic  # noqa: E402

HIDIOCSFEATURE = 0xC0084806
SNAPSHOT_FILE = "/tmp/z13_blob_sweep_snapshot.bin"
BANK_REPORT = 7
BANK_SIZE = 256
LOG_FILE = os.path.expanduser("~/.config/z13-g2-tools/blob-sweep.log")


def read_bank(dev):
    """读报告 7 参数区(256 字节 bytes),失败返回 None。"""
    features = haptic.read_features(dev)
    bank = features.get(BANK_REPORT)
    if bank is None or len(bank) != BANK_SIZE:
        print("读取报告 7 参数区失败", file=sys.stderr)
        return None
    return bank


def write_bank(dev, bank):
    """整块写回报告 7(前置报告号字节)。"""
    fd = os.open(dev, os.O_RDWR)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE, bytes([BANK_REPORT]) + bytes(bank))
    finally:
        os.close(fd)


def save_snapshot(bank):
    """首次写前保存快照;已有快照则不覆盖。"""
    if os.path.exists(SNAPSHOT_FILE):
        print(f"已存在快照 {SNAPSHOT_FILE}(用 --restore 还原)")
        return
    with open(SNAPSHOT_FILE, "wb") as f:
        f.write(bank)
    print(f"已保存原始参数区快照到 {SNAPSHOT_FILE}")


def restore(dev):
    """从快照还原参数区并回读确认。"""
    if not os.path.exists(SNAPSHOT_FILE):
        print("没有快照文件", file=sys.stderr)
        return 1
    with open(SNAPSHOT_FILE, "rb") as f:
        orig = f.read()
    write_bank(dev, orig)
    back = read_bank(dev)
    if back == orig:
        print(f"已从快照还原报告 7 参数区({len(orig)} 字节)")
        return 0
    print("还原后回读与快照不一致!", file=sys.stderr)
    return 1


def log_experiment(offset, value, result):
    """把一轮实验记入 ~/.config/z13-g2-tools/blob-sweep.log。"""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} offset={offset} value=0x{value:02X} {result}\n")
    except OSError as e:
        print(f"写入实验记录失败: {e}", file=sys.stderr)


def sweep(dev, offset, value):
    """单字节实验:改一个字节 → 回读确认 → 提示人工测试 → 还原。"""
    bank = read_bank(dev)
    if bank is None:
        return 1
    if not 0 <= offset < BANK_SIZE:
        print(f"offset 必须在 0-{BANK_SIZE - 1} 之间", file=sys.stderr)
        return 1
    if not 0 <= value <= 255:
        print("value 必须在 0-255 之间", file=sys.stderr)
        return 1

    orig = bank[offset]
    save_snapshot(bank)
    new = bytearray(bank)
    new[offset] = value
    write_bank(dev, new)

    back = read_bank(dev)
    if back is None or back[offset] != value:
        print(f"回读 report7[{offset}] = "
              f"{back[offset] if back else '失败'},与写入 {value} 不符,立即还原",
              file=sys.stderr)
        return restore(dev)
    print(f"report7[{offset}] = {orig} -> {value}(回读确认一致)")

    print("请在图形式会话中人工测试触控板行为(点击/拖动/双指/轻扫),")
    print("测试完成后按回车,脚本将自动还原该字节;Ctrl+C 也会先还原。")
    try:
        input(">>> 回车继续... ")
        result = "人工测试完成,已还原"
    except KeyboardInterrupt:
        result = "测试被中断(Ctrl+C),已还原"
        print()
    finally:
        code = restore(dev)
    log_experiment(offset, value, result)
    return code


def dump(dev):
    """列出参数区非零字节。"""
    bank = read_bank(dev)
    if bank is None:
        return 1
    nz = haptic.bank_nonzero(bank)
    if not nz:
        print("报告 7 参数区全零(256 字节)")
        return 0
    print(f"报告 7 参数区非零字节共 {len(nz)} 个:")
    for i in nz:
        print(f"  [{i:3}] 0x{bank[i]:02X}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--device", default=None,
                    help="hidraw 设备(默认自动探测)")
    ap.add_argument("--dump", action="store_true", help="列出非零字节")
    ap.add_argument("--sweep", nargs="+", metavar=("OFFSET", "[VALUE]"),
                    help="单字节实验:OFFSET [VALUE](默认 0xFF)")
    ap.add_argument("--restore", action="store_true", help="从快照还原")
    args = ap.parse_args()

    dev = args.device or haptic.find_device()
    if not dev:
        print("未找到触控板设备", file=sys.stderr)
        return 1

    if args.dump:
        return dump(dev)
    if args.restore:
        return restore(dev)
    if args.sweep:
        try:
            offset = int(args.sweep[0], 0)
        except ValueError:
            print("offset 必须是整数", file=sys.stderr)
            return 1
        value = 0xFF
        if len(args.sweep) > 1:
            try:
                value = int(args.sweep[1], 0)
            except ValueError:
                print("value 必须是整数", file=sys.stderr)
                return 1
        return sweep(dev, offset, value)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
