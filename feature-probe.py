#!/usr/bin/env python3
"""Experimental probe for the Sensel haptic touchpad vendor features.

The touchpad exposes several vendor HID feature reports (see haptic.py /
the GUI "HID 功能" page for the full list). The click force threshold is
NOT exposed as a named feature; it is most likely one of the bytes in the
report-7 parameter bank (usage 0xFF00:0xC5), or reachable through the
report-9 vendor command channel.

This tool lets you read/write single fields safely: it snapshots the
original bank before the first write and can restore it afterwards
(a reboot also resets the device to defaults).

USE AT YOUR OWN RISK - writing unknown fields may change touchpad
behavior until reboot. Never write unless you know what you are testing.

Usage:
  feature-probe.py --dump                     show all feature reports
  feature-probe.py --read N                   show bank byte N (0-255)
  feature-probe.py --write N VAL              set bank byte N to VAL
  feature-probe.py --restore                  restore the saved snapshot
"""

import argparse
import fcntl
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import haptic  # noqa: E402

HIDIOCSFEATURE = 0xC0084806
SNAPSHOT_FILE = "/tmp/z13_bank_snapshot.bin"
BANK_REPORT = 7


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None,
                    help="hidraw device (default: auto-detect)")
    ap.add_argument("--dump", action="store_true", help="show all feature reports")
    ap.add_argument("--read", type=int, metavar="N",
                    help="show bank byte N (0-255)")
    ap.add_argument("--write", nargs=2, metavar=("N", "VAL"),
                    help="set bank byte N (0-255) to VAL (0-255)")
    ap.add_argument("--restore", action="store_true",
                    help="restore the saved bank snapshot")
    args = ap.parse_args()

    dev = args.device or haptic.find_device()
    if not dev:
        print("未找到触控板设备", file=sys.stderr)
        return 1

    if args.dump:
        for rid, data in haptic.read_features(dev).items():
            print(f"r{rid:>2}: {data.hex(' ')}")
        return 0

    if args.restore:
        if os.path.exists(SNAPSHOT_FILE):
            with open(SNAPSHOT_FILE, "rb") as f:
                orig = f.read()
            fd = os.open(dev, os.O_RDWR)
            try:
                fcntl.ioctl(fd, HIDIOCSFEATURE, bytes([BANK_REPORT]) + orig)
            finally:
                os.close(fd)
            print(f"已还原 report 7 参数区（快照 {len(orig)} 字节）")
        else:
            print("没有快照文件", file=sys.stderr)
        return 0

    if args.write is not None:
        n, val = int(args.write[0]), int(args.write[1])
        if not (0 <= n <= 255 and 0 <= val <= 255):
            print("N 和 VAL 都必须在 0-255 之间", file=sys.stderr)
            return 1
        features = haptic.read_features(dev)
        cur = features.get(BANK_REPORT)
        if cur is None:
            print("读取 report 7 失败", file=sys.stderr)
            return 1
        if not os.path.exists(SNAPSHOT_FILE):
            with open(SNAPSHOT_FILE, "wb") as f:
                f.write(cur)
            print(f"已保存原始参数区快照到 {SNAPSHOT_FILE}")
        else:
            print("已存在快照（用 --restore 还原）")
        new = bytearray(cur)
        print(f"report7[{n}] = {new[n]} -> {val}")
        new[n] = val
        fd = os.open(dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE, bytes([BANK_REPORT]) + bytes(new))
        finally:
            os.close(fd)
        back = haptic.read_features(dev).get(BANK_REPORT)
        print(f"回读 report7[{n}] = {back[n] if back else '失败'}")
        return 0

    if args.read is not None:
        n = int(args.read)
        cur = haptic.read_features(dev).get(BANK_REPORT)
        if cur is None:
            print("读取 report 7 失败", file=sys.stderr)
            return 1
        print(f"report7[{n}] = {cur[n]}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
